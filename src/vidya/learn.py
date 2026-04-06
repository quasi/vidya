"""Feedback-driven extraction engine (Phase 1).

Triggered by vidya_feedback. Three paths:
  user_correction → create or merge knowledge item (auto-promote, high confidence)
  review_rejected → create or merge knowledge item (auto-promote, medium confidence)
  review_accepted / user_confirmation → boost confidence on matching items;
                                        if no match, create pending candidate
  test_failed → decay confidence on matching items;
                if no match, create pending diagnostic candidate
"""

import json
import sqlite3
from typing import Any

from vidya.confidence import SOURCE_CONFIDENCE, update_on_success, update_on_failure
from vidya.query import _sanitize_fts_tokens
from vidya.store import create_item, create_candidate, promote_candidate, update_item

# Feedback types that trigger auto-promotion (corrections are high-quality signal)
_CORRECTION_TYPES = frozenset({"user_correction"})
_REJECTION_TYPES = frozenset({"review_rejected"})
_POSITIVE_TYPES = frozenset({"review_accepted", "user_confirmation"})
_FAILURE_TYPES = frozenset({"test_failed"})

# Overlap threshold for deduplication (0-1).
# Measures fraction of new-feedback tokens found in existing item text.
# 0.5 is appropriate for Phase 1 simple token matching; Phase 3 will use embeddings.
_MERGE_THRESHOLD = 0.5


def extract_from_feedback(
    db: sqlite3.Connection,
    feedback: dict[str, Any],
    task: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Process a feedback record and update the knowledge base.

    Returns:
      {"item_id": str} if a new item was created.
      {"merged": True, "item_id": str} if merged into an existing item.
      None if no new item was created (positive/failure feedback updates existing items).
    """
    fb_type = feedback["feedback_type"]
    detail = feedback["detail"]
    language = feedback.get("language") or (task.get("language") if task else None)
    project = feedback.get("project") or (task.get("project") if task else None)
    framework = feedback.get("framework") or (task.get("framework") if task else None)
    runtime = feedback.get("runtime") or (task.get("runtime") if task else None)

    if fb_type in _CORRECTION_TYPES:
        return _handle_correction(
            db, feedback["id"], detail, language, runtime, framework, project,
            source="user_correction",
        )
    if fb_type in _REJECTION_TYPES:
        return _handle_correction(
            db, feedback["id"], detail, language, runtime, framework, project,
            source="review_rejected",
        )
    if fb_type in _POSITIVE_TYPES:
        matched = _apply_confidence_update(
            db, detail, language, project, update_on_success, "success_count",
        )
        if not matched:
            return _create_candidate_from_unmatched(
                db, feedback["id"], detail, language, runtime, framework, project,
                source="user_confirmation",
            )
        return None
    if fb_type in _FAILURE_TYPES:
        matched = _apply_confidence_update(
            db, detail, language, project, update_on_failure, "fail_count",
        )
        if not matched:
            return _create_candidate_from_unmatched(
                db, feedback["id"], detail, language, runtime, framework, project,
                source="test_outcome",
                item_type="diagnostic",
            )
        return None
    # test_passed is recorded in feedback_records but does NOT update knowledge items.
    # Passing tests are expected, not evidence of correctness.
    return None


def classify_type(detail: str) -> str:
    """Classify the knowledge item type from feedback or rule text."""
    lower = detail.lower()
    if any(kw in lower for kw in ("don't", "never", "avoid")):
        return "anti_pattern"
    if any(kw in lower for kw in ("after", "then", "verify")):
        return "postcondition"
    if any(kw in lower for kw in ("before", "first", "ensure")):
        return "precondition"
    if any(kw in lower for kw in ("always", "must", "should")):
        return "convention"
    return "convention"


def find_similar_items(
    db: sqlite3.Connection,
    detail: str,
    language: str | None,
    project: str | None,
) -> list[dict[str, Any]]:
    """Return active items that overlap with this text via FTS5."""
    fts_query = _sanitize_fts_tokens(detail)
    if not fts_query:
        return []
    try:
        rows = db.execute(
            """
            SELECT ki.*
            FROM knowledge_items ki
            JOIN knowledge_fts fts ON fts.item_id = ki.id
            WHERE knowledge_fts MATCH ?
              AND ki.status = 'active'
              AND (ki.language IS NULL OR ki.language = ?)
            ORDER BY fts.rank
            LIMIT 5
            """,
            (fts_query, language),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def overlap_score(detail: str, item: dict[str, Any]) -> float:
    """Fraction of new-detail tokens found in the existing item's text.

    Directional containment: how much of the new feedback is already covered
    by the existing item. More lenient than Jaccard for Phase 1 heuristics.
    """
    a_tokens = set(detail.lower().split())
    b_tokens = set((item["guidance"] + " " + item["pattern"]).lower().split())
    if not a_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens)


def _apply_confidence_update(
    db: sqlite3.Connection,
    detail: str,
    language: str | None,
    project: str | None,
    update_fn: Any,
    count_field: str,
) -> bool:
    """Find similar items and apply a confidence update function to each.

    Returns True if at least one item was matched and updated.
    """
    similar = find_similar_items(db, detail, language, project)
    matched = False
    for item in similar:
        if overlap_score(detail, item) > 0.3:
            item_dict = dict(item)
            update_fn(item_dict)
            update_item(
                db,
                item["id"],
                _commit=False,
                base_confidence=item_dict["base_confidence"],
                last_fired=item_dict["last_fired"],
                fire_count=item_dict["fire_count"],
                **{count_field: item_dict[count_field]},
            )
            matched = True
    if matched:
        db.commit()
    return matched


def _handle_correction(
    db: sqlite3.Connection,
    feedback_id: str,
    detail: str,
    language: str | None,
    runtime: str | None,
    framework: str | None,
    project: str | None,
    source: str,
) -> dict[str, Any]:
    """Create or merge a knowledge item for corrections and rejections.

    Auto-promotes immediately — these are high-quality signals.
    The source determines the initial confidence via SOURCE_CONFIDENCE.
    """
    similar = find_similar_items(db, detail, language, project)

    for existing in similar:
        if overlap_score(detail, existing) >= _MERGE_THRESHOLD:
            old_evidence = json.loads(existing.get("evidence") or "[]")
            old_evidence.append(feedback_id)
            item_dict = dict(existing)
            update_on_success(item_dict)
            update_item(
                db,
                existing["id"],
                base_confidence=item_dict["base_confidence"],
                last_fired=item_dict["last_fired"],
                fire_count=item_dict["fire_count"],
                success_count=item_dict["success_count"],
                evidence=json.dumps(old_evidence),
            )
            return {"merged": True, "item_id": existing["id"]}

    # New candidate → auto-promote (feedback is high-quality signal)
    item_type = classify_type(detail)
    candidate_id = create_candidate(
        db,
        pattern=_infer_pattern(detail, language, project),
        guidance=detail,
        item_type=item_type,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
        method="feedback",
        evidence=json.dumps([feedback_id]),
        initial_confidence=SOURCE_CONFIDENCE[source],
    )
    item_id = promote_candidate(db, candidate_id, source=source)
    return {"item_id": item_id}


def _find_similar_candidates(
    db: sqlite3.Connection,
    detail: str,
) -> list[dict[str, Any]]:
    """Return pending candidates that overlap with this text."""
    rows = db.execute(
        "SELECT * FROM extraction_candidates WHERE status = 'pending'"
    ).fetchall()
    return [dict(r) for r in rows]


def _create_candidate_from_unmatched(
    db: sqlite3.Connection,
    feedback_id: str,
    detail: str,
    language: str | None,
    runtime: str | None,
    framework: str | None,
    project: str | None,
    source: str,
    item_type: str | None = None,
) -> dict[str, Any]:
    """Create a pending candidate (no auto-promotion) for unmatched signals.

    Used when confirmations or test failures have no matching item to update.
    Candidates remain 'pending' until reviewed — proliferation guard.
    Deduplicates against existing pending candidates.
    """
    # Check for duplicate pending candidates
    existing = _find_similar_candidates(db, detail)
    for candidate in existing:
        if overlap_score(detail, candidate) >= _MERGE_THRESHOLD:
            old_evidence = json.loads(candidate.get("evidence") or "[]")
            old_evidence.append(feedback_id)
            db.execute(
                "UPDATE extraction_candidates SET evidence = ? WHERE id = ?",
                (json.dumps(old_evidence), candidate["id"]),
            )
            db.commit()
            return {"candidate_id": candidate["id"], "merged": True}

    resolved_type = item_type or classify_type(detail)
    candidate_id = create_candidate(
        db,
        pattern=_infer_pattern(detail, language, project),
        guidance=detail,
        item_type=resolved_type,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
        method="feedback",
        evidence=json.dumps([feedback_id]),
        initial_confidence=SOURCE_CONFIDENCE[source],
    )
    return {"candidate_id": candidate_id}


def _infer_pattern(detail: str, language: str | None, project: str | None) -> str:
    """Derive a pattern string from the feedback detail (Phase 1 heuristic)."""
    words = [w for w in detail.lower().split()
             if w not in ("a", "an", "the", "to", "in", "on", "at", "for", "of", "with")]
    topic = " ".join(words[:8]).rstrip(".,;:")
    if project:
        return f"{topic} in {project}"
    return topic
