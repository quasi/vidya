"""Cascade query with scope resolution, FTS5 ranking, and override suppression."""

import sqlite3
from dataclasses import dataclass


def _sanitize_fts_tokens(text: str) -> str:
    """Quote each token to prevent FTS5 operator interpretation.

    FTS5 treats AND, OR, NOT, *, ^, etc. as operators.
    Double-quoting each token forces literal matching.
    """
    tokens = text.split()
    if not tokens:
        return ""
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " OR ".join(quoted)


# Scope specificity boost: narrower scope → higher score
_SCOPE_BOOST: dict[str, float] = {
    "project": 1.5,
    "framework": 1.3,
    "runtime": 1.1,
    "language": 1.0,
    "global": 0.9,
}


def _scope_level(row: dict) -> str:
    if row["project"]:
        return "project"
    if row["framework"]:
        return "framework"
    if row["runtime"]:
        return "runtime"
    if row["language"]:
        return "language"
    return "global"


@dataclass
class QueryResult:
    id: str
    pattern: str
    guidance: str
    type: str
    effective_confidence: float
    scope_level: str
    match_reason: str
    match_source: str | None = None      # "bundle" when grouped
    bundle_member_count: int | None = None  # count of grouped items


def cascade_query(
    db: sqlite3.Connection,
    context: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    min_confidence: float = 0.2,
) -> list[QueryResult]:
    """Return ranked knowledge items relevant to the given context.

    Algorithm:
    1. Fetch active items matching the applicable scope levels.
    2. Filter by min_confidence (base_confidence used directly as effective_confidence).
    3. FTS5 match on context (and goal) for relevance ranking.
    4. score = relevance * effective_confidence * scope_boost
    5. Override suppression: if item A overrides item B, drop B.
    6. Sort by score descending, build match_reason.
    """
    # Step 1: fetch all active items in scope
    rows = _fetch_in_scope(db, language, runtime, framework, project)

    # Step 2 + 3: filter by base_confidence directly
    candidates = []
    for row in rows:
        eff = row["base_confidence"]
        if eff < min_confidence:
            continue
        candidates.append((dict(row), eff))

    if not candidates:
        return []

    # Step 4: FTS5 relevance for each candidate
    fts_scores = _fts_scores(db, context, goal)

    # Step 5: compute final score
    # If there are FTS matches, use them to filter AND rank.
    # If there are no FTS matches at all (empty context etc.), return all in-scope items.
    has_fts_matches = bool(fts_scores)

    # Items scoring below this fraction of the best FTS score are stopword noise,
    # not real matches. FTS5 assigns tiny non-zero scores (~1e-7) to items that
    # happen to contain common words ("the", "and") in the query — exact equality
    # to 0.0 misses these, so use a meaningful minimum instead.
    FTS_NOISE_THRESHOLD = 0.05

    scored = []
    for row, eff in candidates:
        fts = fts_scores.get(row["id"], 0.0)
        if has_fts_matches and fts < FTS_NOISE_THRESHOLD:
            # FTS matches exist but this item scored below noise floor — skip it
            continue
        scope = _scope_level(row)
        boost = _SCOPE_BOOST[scope]
        score = (1.0 + fts) * eff * boost
        scored.append((row, eff, scope, score, fts))

    # Step 6: override suppression — collect overridden IDs
    overridden_ids: set[str] = set()
    for row, _, _, _, _ in scored:
        if row["overrides"]:
            overridden_ids.add(row["overrides"])

    # Step 7: sort, suppress, build results
    scored.sort(key=lambda t: t[3], reverse=True)

    results = []
    for row, eff, scope, score, fts in scored:
        if row["id"] in overridden_ids:
            continue
        reason = _build_reason(row, scope, fts)
        results.append(QueryResult(
            id=row["id"],
            pattern=row["pattern"],
            guidance=row["guidance"],
            type=row["type"],
            effective_confidence=eff,
            scope_level=scope,
            match_reason=reason,
        ))

    return _group_by_bundle(db, results)


def _group_by_bundle(
    db: sqlite3.Connection,
    results: list[QueryResult],
) -> list[QueryResult]:
    """Compact results sharing a bundle_id into a single bundle QueryResult.

    Presentation-layer only — the FTS retrieval is untouched.
    For each group whose bundle is active, replace the group with the bundle item.
    If the bundle is not active (superseded/archived), pass sources through unchanged.
    Items without a bundle_id are returned as-is.
    Sort order is preserved: the bundle result takes the position of its highest-ranked member.
    """
    if not results:
        return results

    result_ids = [r.id for r in results]

    # Query bundle_id for all result items in one shot
    placeholders = ",".join("?" * len(result_ids))
    bundle_rows = db.execute(
        f"SELECT id, bundle_id FROM knowledge_items WHERE id IN ({placeholders}) AND bundle_id IS NOT NULL",
        result_ids,
    ).fetchall()

    if not bundle_rows:
        return results

    # Map item_id → bundle_id for items that have one
    item_to_bundle: dict[str, str] = {row["id"]: row["bundle_id"] for row in bundle_rows}

    # Group result objects by bundle_id, preserving position index
    bundle_groups: dict[str, list[tuple[int, QueryResult]]] = {}
    for idx, result in enumerate(results):
        bid = item_to_bundle.get(result.id)
        if bid is not None:
            bundle_groups.setdefault(bid, []).append((idx, result))

    # For each bundle group, decide: replace with bundle item or pass through
    replacements: dict[int, QueryResult] = {}  # position → replacement result
    suppressed_positions: set[int] = set()

    for bundle_id, indexed_members in bundle_groups.items():
        # Fetch the bundle item — must be active
        bundle_row = db.execute(
            "SELECT id, pattern, guidance, type, base_confidence, language, runtime, framework, project "
            "FROM knowledge_items WHERE id = ? AND status = 'active'",
            (bundle_id,),
        ).fetchone()

        if bundle_row is None:
            # Bundle is not active (superseded/archived) — pass sources through unchanged
            continue

        # Replace the group with a single bundle result.
        # Find the earliest position among: the source items AND the bundle item itself
        # (bundle may also appear in results from its own FTS match).
        source_positions = [idx for idx, _ in indexed_members]
        member_results = [r for _, r in indexed_members]

        # Check if the bundle item itself is in results
        bundle_item_id = bundle_row["id"]
        bundle_in_results_positions = [
            pos for pos, r in enumerate(results) if r.id == bundle_item_id
        ]

        all_positions = source_positions + bundle_in_results_positions
        insertion_pos = min(all_positions)
        extra_positions = set(all_positions) - {insertion_pos}

        max_confidence = max(r.effective_confidence for r in member_results)
        first = member_results[0]
        n = len(member_results)

        bundle_result = QueryResult(
            id=bundle_row["id"],
            pattern=bundle_row["pattern"],
            guidance=bundle_row["guidance"],
            type=bundle_row["type"],
            effective_confidence=max_confidence,
            scope_level=first.scope_level,
            match_reason=first.match_reason + f" (bundled {n} items)",
            match_source="bundle",
            bundle_member_count=n,
        )

        replacements[insertion_pos] = bundle_result
        suppressed_positions.update(extra_positions)

    if not replacements and not suppressed_positions:
        return results

    final: list[QueryResult] = []
    for idx, result in enumerate(results):
        if idx in suppressed_positions:
            continue
        if idx in replacements:
            final.append(replacements[idx])
        else:
            final.append(result)

    return final


def _fetch_in_scope(
    db: sqlite3.Connection,
    language: str | None,
    runtime: str | None,
    framework: str | None,
    project: str | None,
) -> list:
    """Fetch active items applicable to this scope (all matching scope levels)."""
    # An item is in scope if:
    #   - global (all scope columns NULL), OR
    #   - language matches (and runtime/framework/project are NULL in item), OR
    #   - runtime matches (and framework/project NULL in item), OR
    #   - framework matches (language-specific or language-independent tool knowledge), OR
    #   - project matches
    # We use a single query with OR conditions per scope level.
    params = []
    scope_clauses = ["(language IS NULL AND runtime IS NULL AND framework IS NULL AND project IS NULL)"]

    if language:
        # Project is a sub-scope of language: (python, vidya) items appear when querying language=python
        scope_clauses.append("(language = ? AND runtime IS NULL AND framework IS NULL)")
        params.append(language)

        if runtime:
            scope_clauses.append("(language = ? AND runtime = ? AND framework IS NULL)")
            params.extend([language, runtime])

    if framework:
        # Language-independent tool knowledge (e.g. framework=canon with language=NULL)
        scope_clauses.append("(language IS NULL AND runtime IS NULL AND framework = ? AND project IS NULL)")
        params.append(framework)
        if language:
            # Language-specific framework knowledge — strict: python+canon ≠ rust+canon
            scope_clauses.append("(language = ? AND runtime IS NULL AND framework = ?)")
            params.extend([language, framework])
            if runtime:
                scope_clauses.append("(language = ? AND runtime = ? AND framework = ?)")
                params.extend([language, runtime, framework])

    if project:
        # All items for this project, regardless of language.
        # project=vidya returns (python, vidya) items even without --language python.
        scope_clauses.append("(project = ?)")
        params.append(project)

    where = " OR ".join(scope_clauses)
    sql = f"SELECT * FROM knowledge_items WHERE status = 'active' AND ({where})"
    return db.execute(sql, params).fetchall()


def _fts_scores(
    db: sqlite3.Connection,
    context: str,
    goal: str | None,
) -> dict[str, float]:
    """Return FTS5 relevance scores keyed by item_id. Missing items score 0."""
    # FTS5 rank() returns a negative float; higher magnitude = more relevant.
    # We invert to a positive score.
    # Tokenize context + goal into individual words, joined with OR for partial matching.
    # FTS5 AND logic ("error handling" requires both) is too strict;
    # OR gives partial semantic overlap.
    combined = context
    if goal:
        combined += " " + goal
    fts_query = _sanitize_fts_tokens(combined)
    if not fts_query:
        return {}

    try:
        rows = db.execute(
            "SELECT item_id, rank FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank",
            (fts_query,),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 parse error (e.g. special chars in context) — fall back to unranked
        return {}

    scores: dict[str, float] = {}
    for row in rows:
        # rank is negative; convert to positive relevance (closer to 0 = less relevant)
        scores[row[0]] = abs(row[1]) if row[1] is not None else 0.0

    # Normalize to 0-1 range
    if scores:
        max_score = max(scores.values())
        if max_score > 0:
            scores = {k: v / max_score for k, v in scores.items()}

    return scores


def _build_reason(row: dict, scope: str, fts_score: float) -> str:
    parts = [f"scope={scope}"]
    if row["language"]:
        parts.append(f"language={row['language']}")
    if row["project"]:
        parts.append(f"project={row['project']}")
    if row["framework"]:
        parts.append(f"framework={row['framework']}")
    if row["runtime"]:
        parts.append(f"runtime={row['runtime']}")
    if fts_score > 0:
        parts.append(f"fts_match={fts_score:.2f}")
    return ", ".join(parts)
