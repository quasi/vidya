"""Maintenance: stats and freshness computation.

Phase 1: compute_stats only. Capacity eviction and drift detection are Phase 2.
Freshness is computed at query time (not batch-updated) — see confidence.py.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vidya.confidence import compute_freshness, days_since_reference, effective_confidence
from vidya.store import archive_item


@dataclass
class Stats:
    total_items: int = 0
    by_confidence: dict[str, int] = field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0})
    by_type: dict[str, int] = field(default_factory=dict)
    by_scope: dict[str, int] = field(
        default_factory=lambda: {"global": 0, "language": 0, "runtime": 0, "framework": 0, "project": 0}
    )
    total_tasks: int = 0
    total_feedback: int = 0
    total_candidates: int = 0


def compute_stats(
    db: sqlite3.Connection,
    language: str | None = None,
    project: str | None = None,
) -> Stats:
    """Compute knowledge base statistics, optionally filtered by language/project."""
    stats = Stats()
    now = datetime.now(timezone.utc)

    # Build optional filter
    conditions = ["status = 'active'"]
    params: list = []
    if language:
        conditions.append("language = ?")
        params.append(language)
    if project:
        conditions.append("project = ?")
        params.append(project)
    where = " AND ".join(conditions)

    rows = db.execute(
        f"SELECT base_confidence, last_fired, first_seen, type, "
        f"language, runtime, framework, project FROM knowledge_items WHERE {where}",
        params,
    ).fetchall()

    stats.total_items = len(rows)

    for row in rows:
        days = days_since_reference(row["last_fired"], row["first_seen"], now)
        fresh = compute_freshness(days)
        eff = effective_confidence(row["base_confidence"], fresh)

        # Confidence band
        if eff > 0.5:
            stats.by_confidence["high"] += 1
        elif eff >= 0.2:
            stats.by_confidence["medium"] += 1
        else:
            stats.by_confidence["low"] += 1

        # Type distribution
        item_type = row["type"] or "unknown"
        stats.by_type[item_type] = stats.by_type.get(item_type, 0) + 1

        # Scope distribution
        if row["project"]:
            stats.by_scope["project"] += 1
        elif row["framework"]:
            stats.by_scope["framework"] += 1
        elif row["runtime"]:
            stats.by_scope["runtime"] += 1
        elif row["language"]:
            stats.by_scope["language"] += 1
        else:
            stats.by_scope["global"] += 1

    # Counts from other tables
    task_where = ""
    task_params: list = []
    if language:
        task_where = " WHERE language = ?"
        task_params.append(language)
    if project:
        sep = " AND " if task_where else " WHERE "
        task_where += f"{sep}project = ?"
        task_params.append(project)

    stats.total_tasks = db.execute(
        f"SELECT COUNT(*) FROM task_records{task_where}", task_params
    ).fetchone()[0]

    stats.total_feedback = db.execute(
        f"SELECT COUNT(*) FROM feedback_records{task_where}", task_params
    ).fetchone()[0]

    stats.total_candidates = db.execute(
        "SELECT COUNT(*) FROM extraction_candidates"
    ).fetchone()[0]

    return stats


def find_stale_items(
    db: sqlite3.Connection,
    language: str | None = None,
    project: str | None = None,
    stale_days: int = 90,
    min_confidence: float = 0.2,
) -> list[dict[str, Any]]:
    """Find items that are stale: unfired for too long, or below confidence threshold."""
    now = datetime.now(timezone.utc)
    conditions = ["status = 'active'"]
    params: list = []
    if language:
        conditions.append("language = ?")
        params.append(language)
    if project:
        conditions.append("project = ?")
        params.append(project)
    where = " AND ".join(conditions)

    rows = db.execute(
        f"SELECT id, pattern, guidance, base_confidence, last_fired, first_seen, "
        f"fire_count FROM knowledge_items WHERE {where}",
        params,
    ).fetchall()

    stale: list[dict[str, Any]] = []
    for row in rows:
        days = days_since_reference(row["last_fired"], row["first_seen"], now)
        fresh = compute_freshness(days)
        eff = effective_confidence(row["base_confidence"], fresh)

        reasons = []
        if row["fire_count"] == 0 and days >= stale_days:
            reasons.append(f"Never fired, created {int(days)} days ago")
        elif row["fire_count"] > 0 and days >= stale_days:
            reasons.append(f"Last fired {int(days)} days ago")
        if eff < min_confidence:
            reasons.append(f"Effective confidence {eff:.3f} below {min_confidence}")

        if reasons:
            stale.append({
                "id": row["id"],
                "pattern": row["pattern"],
                "guidance": row["guidance"],
                "reason": "; ".join(reasons),
                "effective_confidence": round(eff, 3),
                "days_since_activity": int(days),
            })

    stale.sort(key=lambda x: x["effective_confidence"])
    return stale


def auto_archive_stale(
    db: sqlite3.Connection,
    language: str | None = None,
    project: str | None = None,
    dry_run: bool = True,
    archive_threshold: float = 0.1,
) -> dict[str, Any]:
    """Archive items with effective confidence below archive_threshold.

    Args:
        dry_run: If True, report what would be archived without doing it.
        archive_threshold: Items with effective_confidence below this get archived.
    """
    # find_stale_items returns items stale for ANY reason (age OR low confidence).
    # The candidates filter then narrows to those below archive_threshold.
    # This two-step is intentional: archive_threshold (0.1) < health_report's
    # min_confidence (0.2), so we only archive the worst items, not all stale ones.
    stale = find_stale_items(db, language=language, project=project, min_confidence=archive_threshold)
    candidates = [s for s in stale if s["effective_confidence"] < archive_threshold]

    if dry_run:
        return {
            "archived_count": 0,
            "archived_ids": [],
            "would_archive_count": len(candidates),
            "would_archive": candidates,
        }

    archived_ids = []
    for item in candidates:
        archive_item(db, item["id"], reason=f"auto-archive: {item['reason']}")
        archived_ids.append(item["id"])

    return {
        "archived_count": len(archived_ids),
        "archived_ids": archived_ids,
        "would_archive_count": 0,
        "would_archive": [],
    }


def health_report(
    db: sqlite3.Connection,
    language: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Compute a health report for the knowledge base."""
    stats = compute_stats(db, language=language, project=project)
    stale = find_stale_items(db, language=language, project=project)

    if stats.total_items == 0:
        health = "empty"
    elif len(stale) > stats.total_items * 0.5:
        health = "degraded"
    else:
        health = "healthy"

    return {
        "total_items": stats.total_items,
        "by_confidence": stats.by_confidence,
        "by_type": stats.by_type,
        "total_tasks": stats.total_tasks,
        "total_feedback": stats.total_feedback,
        "stale_count": len(stale),
        "stale_items": stale,
        "health": health,
    }
