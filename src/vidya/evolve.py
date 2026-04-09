"""Knowledge evolution: cluster detection and promotion lifecycle.

Finds groups of knowledge items that share significant vocabulary overlap
within the same scope triple (language, framework, project), and provides
functions to promote or reject evolution candidates.
"""

import json
import sqlite3
import string
from collections import deque
from dataclasses import dataclass
from itertools import combinations
from typing import Any


@dataclass
class Cluster:
    """A group of knowledge items sharing a common theme within a scope."""

    item_ids: list[str]
    scope: dict[str, Any]
    cohesion: float
    theme_tokens: list[str]


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    translator = str.maketrans("", "", string.punctuation)
    return set(text.lower().translate(translator).split())


def _pairwise_overlap(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Symmetric overlap: shared tokens / min(len(a), len(b)).

    Unlike the directional overlap_score in learn.py, this is symmetric —
    the smaller set drives the denominator, making it size-independent.
    """
    min_len = min(len(tokens_a), len(tokens_b))
    if min_len == 0:
        return 0.0
    return len(tokens_a & tokens_b) / min_len


def detect_clusters(
    db: sqlite3.Connection,
    language: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    min_size: int = 3,
    overlap_threshold: float = 0.4,
    min_cohesion: float = 0.5,
) -> list[Cluster]:
    """Detect clusters of thematically related knowledge items within a scope.

    Args:
        db: Open SQLite connection with the Vidya schema.
        language: Filter to items with this language (None = all languages).
        framework: Filter to items with this framework (None = all frameworks).
        project: Filter to items with this project (None = all projects).
        min_size: Minimum cluster size to report.
        overlap_threshold: Minimum symmetric overlap for two items to be adjacent.
        min_cohesion: Minimum average pairwise overlap to accept a cluster.

    Returns:
        List of Cluster dataclasses, one per accepted component.
    """
    # --- 1. Query active items matching the scope filter ---
    # Exclude bundle items — they share vocabulary with their sources and
    # would inflate cohesion / create re-clustering noise.
    conditions = ["status = 'active'", "type != 'bundle'"]
    params: list[Any] = []

    if language is not None:
        conditions.append("language = ?")
        params.append(language)
    if framework is not None:
        conditions.append("framework = ?")
        params.append(framework)
    if project is not None:
        conditions.append("project = ?")
        params.append(project)

    where = " AND ".join(conditions)
    rows = db.execute(
        f"SELECT id, language, framework, project, pattern, guidance FROM knowledge_items WHERE {where}",
        params,
    ).fetchall()

    if not rows:
        return []

    # --- 2. Tokenize each item ---
    items = [dict(r) for r in rows]
    tokens_by_id: dict[str, set[str]] = {}
    for item in items:
        text = (item["pattern"] or "") + " " + (item["guidance"] or "")
        tokens_by_id[item["id"]] = _tokenize(text)

    # --- 3. Group items by exact scope triple ---
    scope_groups: dict[tuple, list[dict]] = {}
    for item in items:
        key = (item["language"], item["framework"], item["project"])
        scope_groups.setdefault(key, []).append(item)

    results: list[Cluster] = []

    for scope_key, group in scope_groups.items():
        if len(group) < min_size:
            continue

        # --- 4. Build adjacency (pairwise overlap >= threshold) ---
        adjacency: dict[str, set[str]] = {item["id"]: set() for item in group}
        for item_a, item_b in combinations(group, 2):
            id_a, id_b = item_a["id"], item_b["id"]
            ov = _pairwise_overlap(tokens_by_id[id_a], tokens_by_id[id_b])
            if ov >= overlap_threshold:
                adjacency[id_a].add(id_b)
                adjacency[id_b].add(id_a)

        # --- 5. Extract connected components via BFS ---
        visited: set[str] = set()
        components: list[list[str]] = []

        for start_id in adjacency:
            if start_id in visited:
                continue
            component: list[str] = []
            queue: deque[str] = deque([start_id])
            visited.add(start_id)
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbour in adjacency[current]:
                    if neighbour not in visited:
                        visited.add(neighbour)
                        queue.append(neighbour)
            components.append(component)

        # --- 6 & 7. Compute cohesion, reject below min_cohesion ---
        scope_dict = {
            "language": scope_key[0],
            "framework": scope_key[1],
            "project": scope_key[2],
        }

        for component in components:
            if len(component) < min_size:
                continue

            pairs = list(combinations(component, 2))
            if not pairs:
                continue

            total_overlap = sum(
                _pairwise_overlap(tokens_by_id[a], tokens_by_id[b])
                for a, b in pairs
            )
            cohesion = total_overlap / len(pairs)

            if cohesion < min_cohesion:
                continue

            # --- 8. Compute centroid tokens (appear in >50% of members) ---
            threshold_count = len(component) / 2
            token_counts: dict[str, int] = {}
            for item_id in component:
                for token in tokens_by_id[item_id]:
                    token_counts[token] = token_counts.get(token, 0) + 1

            theme_tokens = sorted(
                token for token, count in token_counts.items()
                if count > threshold_count
            )

            results.append(
                Cluster(
                    item_ids=component,
                    scope=scope_dict,
                    cohesion=cohesion,
                    theme_tokens=theme_tokens,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Evolution lifecycle
# ---------------------------------------------------------------------------

def promote_candidate(
    db: sqlite3.Connection,
    candidate_id: str,
    edited_guidance: str | None = None,
) -> str:
    """Promote an evolution candidate to a bundle knowledge item.

    1. Reads the candidate from evolution_candidates.
    2. Reads source items and averages their base_confidence.
    3. Creates a new 'bundle' knowledge item with source='evolution'.
    4. Tags each source item with the new bundle's ID.
    5. Marks the candidate as 'promoted'.

    Returns the new bundle item ID.
    """
    from vidya.store import create_item, update_item  # local import avoids circular deps

    # All reads and writes in one transaction
    with db:
        # 1. Read candidate
        cand = db.execute(
            "SELECT * FROM evolution_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if cand is None:
            raise KeyError(f"Evolution candidate not found: {candidate_id}")
        if cand["status"] != "pending":
            raise ValueError(f"Candidate {candidate_id} has status '{cand['status']}', expected 'pending'")

        source_ids: list[str] = json.loads(cand["source_item_ids"])

        # 2. Compute average base_confidence from source items
        rows = db.execute(
            f"SELECT base_confidence FROM knowledge_items WHERE id IN ({','.join('?' * len(source_ids))})",
            source_ids,
        ).fetchall()
        if rows:
            avg_confidence = sum(r["base_confidence"] for r in rows) / len(rows)
        else:
            avg_confidence = 0.0

        guidance = edited_guidance if edited_guidance is not None else cand["guidance"]

        # 3. Create bundle item
        bundle_id = create_item(
            db,
            pattern=cand["pattern"],
            guidance=guidance,
            item_type="bundle",
            language=cand["scope_language"],
            framework=cand["scope_framework"],
            project=cand["scope_project"],
            base_confidence=avg_confidence,
            source="evolution",
            _commit=False,
        )

        # Set related_items on the bundle
        update_item(db, bundle_id, related_items=json.dumps(source_ids), _commit=False)

        # 4. Tag each source item with the bundle_id
        for sid in source_ids:
            update_item(db, sid, bundle_id=bundle_id, _commit=False)

        # 5. Mark candidate as promoted
        db.execute(
            "UPDATE evolution_candidates SET status = 'promoted' WHERE id = ?",
            (candidate_id,),
        )

    return bundle_id


def reject_candidate(db: sqlite3.Connection, candidate_id: str) -> None:
    """Mark an evolution candidate as rejected. Source items are not modified."""
    cursor = db.execute(
        "UPDATE evolution_candidates SET status = 'rejected' WHERE id = ?",
        (candidate_id,),
    )
    if cursor.rowcount == 0:
        raise KeyError(f"Evolution candidate not found: {candidate_id}")
    db.commit()
