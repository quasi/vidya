"""Cascade query with scope resolution, FTS5 ranking, and override suppression."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from vidya.confidence import compute_freshness, effective_confidence


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


def _days_since(iso_ts: str | None, now: datetime) -> int | None:
    if iso_ts is None:
        return None
    last = datetime.fromisoformat(iso_ts)
    # Defensive: handle legacy rows without tz offset
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0, (now - last).days)


@dataclass
class QueryResult:
    id: str
    pattern: str
    guidance: str
    type: str
    effective_confidence: float
    scope_level: str
    match_reason: str


def cascade_query(
    db: sqlite3.Connection,
    context: str,
    language: str,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    min_confidence: float = 0.2,
) -> list[QueryResult]:
    """Return ranked knowledge items relevant to the given context.

    Algorithm:
    1. Fetch active items matching the applicable scope levels.
    2. Compute effective_confidence = base_confidence * freshness for each.
    3. Filter by min_confidence.
    4. FTS5 match on context (and goal) for relevance ranking.
    5. score = relevance * effective_confidence * scope_boost
    6. Override suppression: if item A overrides item B, drop B.
    7. Sort by score descending, build match_reason.
    """
    # Step 1: fetch all active items in scope
    rows = _fetch_in_scope(db, language, runtime, framework, project)

    now = datetime.now(timezone.utc)  # compute once for the whole query

    # Step 2 + 3: compute effective_confidence, filter
    candidates = []
    for row in rows:
        # Use last_fired if available; fall back to first_seen so newly created
        # items are treated as fresh rather than stale (last_fired=NULL).
        ref_ts = row["last_fired"] or row["first_seen"]
        days = _days_since(ref_ts, now)
        fresh = compute_freshness(days)
        eff = effective_confidence(row["base_confidence"], fresh)
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

    scored = []
    for row, eff in candidates:
        fts = fts_scores.get(row["id"], 0.0)
        if has_fts_matches and fts == 0.0:
            # FTS matches exist but this item didn't match — skip it
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

    return results


def _fetch_in_scope(
    db: sqlite3.Connection,
    language: str,
    runtime: str | None,
    framework: str | None,
    project: str | None,
) -> list:
    """Fetch active items applicable to this scope (all matching scope levels)."""
    # An item is in scope if:
    #   - global (all scope columns NULL), OR
    #   - language matches (and runtime/framework/project are NULL in item), OR
    #   - runtime matches (and framework/project NULL in item), OR
    #   - framework matches (and project NULL in item), OR
    #   - project matches
    # We use a single query with OR conditions per scope level.
    params = []
    scope_clauses = ["(language IS NULL AND runtime IS NULL AND framework IS NULL AND project IS NULL)"]

    scope_clauses.append("(language = ? AND runtime IS NULL AND framework IS NULL AND project IS NULL)")
    params.append(language)

    if runtime:
        scope_clauses.append("(language = ? AND runtime = ? AND framework IS NULL AND project IS NULL)")
        params.extend([language, runtime])

    if framework:
        scope_clauses.append("(language = ? AND runtime IS NULL AND framework = ? AND project IS NULL)")
        params.extend([language, framework])
        if runtime:
            scope_clauses.append("(language = ? AND runtime = ? AND framework = ? AND project IS NULL)")
            params.extend([language, runtime, framework])

    if project:
        scope_clauses.append("(language = ? AND project = ?)")
        params.extend([language, project])

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
    tokens = context.split()
    if goal:
        tokens.extend(goal.split())
    if not tokens:
        return {}
    fts_query = " OR ".join(tokens)

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
