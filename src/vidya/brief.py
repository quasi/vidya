"""Structured context dump for vidya_brief MCP tool.

Returns rich data the calling LLM can reason over.
No prose synthesis — the agent IS the LLM.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Any

from vidya.confidence import compute_freshness, days_since_reference, effective_confidence


def assemble_brief(
    db: sqlite3.Connection,
    language: str | None = None,
    framework: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Assemble a structured brief for the calling agent."""
    rows = _fetch_scoped_items(db, language, framework, project)
    return {
        "project_state": _project_state(db, language, framework, project, rows),
        "attention_items": _attention_items(rows),
        "input_quality_hints": _input_quality_hints(),
    }


def _fetch_scoped_items(
    db: sqlite3.Connection,
    language: str | None,
    framework: str | None,
    project: str | None,
) -> list:
    """Fetch active knowledge items for the given scope (shared by state and attention)."""
    conditions = ["status = 'active'"]
    params: list[Any] = []
    if language:
        conditions.append("language = ?")
        params.append(language)
    if framework:
        conditions.append("framework = ?")
        params.append(framework)
    if project:
        conditions.append("project = ?")
        params.append(project)
    where = " AND ".join(conditions)
    return db.execute(
        f"SELECT id, pattern, guidance, base_confidence, last_fired, first_seen, "
        f"type, fire_count, fail_count, success_count "
        f"FROM knowledge_items WHERE {where}",
        params,
    ).fetchall()


def _project_state(
    db: sqlite3.Connection,
    language: str | None,
    framework: str | None,
    project: str | None,
    rows: list,
) -> dict[str, Any]:
    """Counts and distributions for the current scope."""
    now = datetime.now(timezone.utc)

    high = medium = low = 0
    by_type: dict[str, int] = {}
    never_fired = 0

    for row in rows:
        days = days_since_reference(row["last_fired"], row["first_seen"], now)
        fresh = compute_freshness(days)
        eff = effective_confidence(row["base_confidence"], fresh)

        if eff > 0.5:
            high += 1
        elif eff >= 0.2:
            medium += 1
        else:
            low += 1

        t = row["type"] or "unknown"
        by_type[t] = by_type.get(t, 0) + 1

        if row["fire_count"] == 0:
            never_fired += 1

    # Task/feedback scope filtering
    task_where = ""
    task_params: list[Any] = []
    if language:
        task_where = " WHERE language = ?"
        task_params.append(language)
    if framework:
        sep = " AND " if task_where else " WHERE "
        task_where += f"{sep}framework = ?"
        task_params.append(framework)
    if project:
        sep = " AND " if task_where else " WHERE "
        task_where += f"{sep}project = ?"
        task_params.append(project)

    total_tasks = db.execute(
        f"SELECT COUNT(*) FROM task_records{task_where}", task_params
    ).fetchone()[0]

    total_feedback = db.execute(
        f"SELECT COUNT(*) FROM feedback_records{task_where}", task_params
    ).fetchone()[0]

    # Last task outcome
    last_task = db.execute(
        f"SELECT outcome FROM task_records{task_where} ORDER BY timestamp_start DESC LIMIT 1",
        task_params,
    ).fetchone()
    last_task_outcome = last_task["outcome"] if last_task else None

    return {
        "total_items": len(rows),
        "high": high,
        "medium": medium,
        "low": low,
        "never_fired": never_fired,
        "by_type": by_type,
        "total_tasks": total_tasks,
        "total_feedback": total_feedback,
        "last_task_outcome": last_task_outcome,
    }


def _attention_items(rows: list) -> list[dict[str, str]]:
    """Items that need attention: never fired, high failure rate, very stale."""
    attention: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)

    for row in rows:
        # Never fired
        if row["fire_count"] == 0:
            attention.append({
                "id": row["id"],
                "pattern": row["pattern"],
                "reason": "Never fired — unvalidated in practice.",
            })
            continue

        # High failure rate
        if row["fire_count"] >= 3 and row["fail_count"] > row["success_count"]:
            rate = round(100 * row["fail_count"] / row["fire_count"])
            attention.append({
                "id": row["id"],
                "pattern": row["pattern"],
                "reason": f"High failure rate: {rate}% ({row['fail_count']}/{row['fire_count']}). Consider correcting or archiving.",
            })
            continue

        # Very stale (freshness at floor)
        ref_ts = row["last_fired"] or row["first_seen"]
        if ref_ts:
            last = datetime.fromisoformat(ref_ts)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days = (now - last).days
            if days >= 140:
                attention.append({
                    "id": row["id"],
                    "pattern": row["pattern"],
                    "reason": f"Stale: not fired in {days} days. Freshness at floor (0.3).",
                })

    return attention


def _input_quality_hints() -> dict[str, str]:
    """Static hints for writing effective Vidya inputs."""
    return {
        "context": (
            "FTS matches on individual words joined with OR. "
            "Use specific technical terms: 'error handling pytest' works well. "
            "'I need to fix the test errors' matches poorly — too many stopwords."
        ),
        "feedback_detail": (
            "Write the rule you wish existed. Imperative voice: "
            "'Always use uv run pytest, never bare pytest' — not "
            "'the tests should probably use uv or something'. "
            "The detail text becomes the item's guidance verbatim."
        ),
    }
