"""One-time data migration for confidence model rework.

Extraction items were created from user corrections but got source='extraction'
and base_confidence=0.15. This migration fixes source attribution and replays
fire history to compute correct confidence.
"""

import sqlite3

from vidya.confidence import SOURCE_CONFIDENCE, TRUST_GROWTH


def migrate_confidence_model(db: sqlite3.Connection) -> dict:
    """Migrate existing extraction items to correct source and confidence.

    1. Set source to 'user_correction' (verified: all 62 negative feedbacks
       in the database are user_correction, zero review_rejected).
    2. Start from SOURCE_CONFIDENCE['user_correction'] (0.85).
    3. Replay success_count applications of heuristic growth.
    4. Idempotent: only updates items still marked source='extraction'.

    Returns: {"updated_count": N, "details": [...]}
    """
    rows = db.execute(
        "SELECT id, success_count, fail_count FROM knowledge_items "
        "WHERE status = 'active' AND source = 'extraction'"
    ).fetchall()

    updated = []
    for row in rows:
        base = SOURCE_CONFIDENCE["user_correction"]
        for _ in range(row["success_count"]):
            base = base + TRUST_GROWTH * (1.0 - base)

        db.execute(
            "UPDATE knowledge_items SET source = ?, base_confidence = ? WHERE id = ?",
            ("user_correction", base, row["id"]),
        )
        updated.append({"id": row["id"], "new_confidence": round(base, 6),
                         "successes_replayed": row["success_count"]})

    if updated:
        db.commit()

    return {"updated_count": len(updated), "details": updated}
