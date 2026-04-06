"""CRUD operations for all Vidya tables. All functions take db: Connection first."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# Allowlist of columns that callers may update on knowledge_items.
# Prevents SQL column injection via update_item(**fields).
_VALID_OUTCOMES: frozenset[str] = frozenset({
    "success", "partial", "failure", "abandoned",
})

_VALID_FEEDBACK_TYPES: frozenset[str] = frozenset({
    "review_accepted", "review_rejected", "test_passed",
    "test_failed", "user_correction", "user_confirmation",
})

_VALID_ITEM_TYPES: frozenset[str] = frozenset({
    "convention", "anti_pattern", "precondition", "postcondition",
    "heuristic", "diagnostic", "warning", "recovery", "workflow",
})

_VALID_RESULT_STATUSES: frozenset[str] = frozenset({
    "success", "error", "timeout", "partial", "rejected",
})

_VALID_ACTION_TYPES: frozenset[str] = frozenset({
    "tool_call",       # Invoked a tool (file read, write, bash, grep, etc.)
    "decision",        # Chose between alternatives
    "discovery",       # Learned something about the codebase or environment
    "correction",      # Fixed a mistake or changed approach after error
    "attempt",         # Tried something — may succeed or fail
    "delegation",      # Delegated to subagent or external process
    "configuration",   # Changed settings, environment, or config
})


def _validate(value: str, valid: frozenset[str], field_name: str) -> None:
    if value not in valid:
        raise ValueError(f"Invalid {field_name}: {value!r}. Must be one of: {sorted(valid)}")


_ITEM_WRITABLE_COLUMNS: frozenset[str] = frozenset({
    "language", "runtime", "framework", "project",
    "pattern", "guidance", "type",
    "details_json", "tags",
    "base_confidence",
    "source", "evidence", "counter_evidence",
    "last_fired", "fire_count", "success_count", "fail_count",
    "overrides", "superseded_by", "related_items",
    "version", "explanation", "status",
})


# --- task_records ---

def create_task(
    db: sqlite3.Connection,
    goal: str,
    language: str | None = None,
    goal_type: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
) -> str:
    task_id = _new_id()
    db.execute(
        """
        INSERT INTO task_records
            (id, session_id, timestamp_start, goal, goal_type,
             language, runtime, framework, project)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, session_id, _now(), goal, goal_type,
         language, runtime, framework, project),
    )
    db.commit()
    return task_id


def get_task(db: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM task_records WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Task not found: {task_id}")
    return dict(row)


def end_task(
    db: sqlite3.Connection,
    task_id: str,
    outcome: str,
    outcome_detail: str | None = None,
    failure_type: str | None = None,
) -> None:
    _validate(outcome, _VALID_OUTCOMES, "outcome")
    cursor = db.execute(
        """
        UPDATE task_records
        SET outcome = ?, outcome_detail = ?, failure_type = ?, timestamp_end = ?
        WHERE id = ?
        """,
        (outcome, outcome_detail, failure_type, _now(), task_id),
    )
    if cursor.rowcount == 0:
        raise KeyError(f"Task not found: {task_id}")
    db.commit()


# --- step_records ---

def create_step(
    db: sqlite3.Connection,
    task_id: str,
    action_type: str,
    action_name: str,
    result_status: str,
    thought: str | None = None,
    action_args: str | None = None,
    result_output: str | None = None,
    result_error: str | None = None,
    alternatives: str | None = None,
    duration_ms: int = 0,
) -> str:
    _validate(action_type, _VALID_ACTION_TYPES, "action_type")
    _validate(result_status, _VALID_RESULT_STATUSES, "result_status")
    step_id = _new_id()
    # Sequence SELECT + INSERT must be atomic to prevent duplicate sequence numbers.
    with db:
        row = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM step_records WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        sequence = row[0]
        db.execute(
            """
            INSERT INTO step_records
                (id, task_id, sequence, timestamp, thought, action_type, action_name,
                 action_args, result_status, result_output, result_error,
                 alternatives, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (step_id, task_id, sequence, _now(), thought, action_type, action_name,
             action_args, result_status, result_output, result_error,
             alternatives, duration_ms),
        )
    return step_id


def get_step(db: sqlite3.Connection, step_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM step_records WHERE id = ?", (step_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Step not found: {step_id}")
    return dict(row)


# --- feedback_records ---

def create_feedback(
    db: sqlite3.Connection,
    feedback_type: str,
    detail: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
) -> str:
    _validate(feedback_type, _VALID_FEEDBACK_TYPES, "feedback_type")
    feedback_id = _new_id()
    db.execute(
        """
        INSERT INTO feedback_records
            (id, task_id, step_id, timestamp, feedback_type, detail,
             language, runtime, framework, project)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (feedback_id, task_id, step_id, _now(), feedback_type, detail,
         language, runtime, framework, project),
    )
    db.commit()
    return feedback_id


# --- knowledge_items ---

def _insert_item_row(
    db: sqlite3.Connection,
    pattern: str,
    guidance: str,
    item_type: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    base_confidence: float = 0.0,
    source: str = "observation",
    evidence: str = "[]",
    explanation: str | None = None,
    overrides: str | None = None,
) -> str:
    """Insert a knowledge_items row. Does NOT commit — caller controls the transaction."""
    item_id = _new_id()
    db.execute(
        """
        INSERT INTO knowledge_items
            (id, language, runtime, framework, project,
             pattern, guidance, type,
             base_confidence, source, evidence,
             explanation, overrides, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, language, runtime, framework, project,
         pattern, guidance, item_type,
         base_confidence, source, evidence,
         explanation, overrides, _now()),
    )
    return item_id


def create_item(
    db: sqlite3.Connection,
    pattern: str,
    guidance: str,
    item_type: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    base_confidence: float = 0.0,
    source: str = "observation",
    evidence: str = "[]",
    explanation: str | None = None,
    overrides: str | None = None,
    _commit: bool = True,
) -> str:
    """Insert a knowledge item. Set _commit=False to batch multiple inserts."""
    _validate(item_type, _VALID_ITEM_TYPES, "item_type")
    item_id = _insert_item_row(
        db, pattern, guidance, item_type, language, runtime, framework, project,
        base_confidence, source, evidence, explanation, overrides,
    )
    if _commit:
        db.commit()
    return item_id


def get_item(db: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM knowledge_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Knowledge item not found: {item_id}")
    return dict(row)


def update_item(db: sqlite3.Connection, item_id: str, _commit: bool = True, **fields: Any) -> None:
    if not fields:
        return
    invalid = set(fields) - _ITEM_WRITABLE_COLUMNS
    if invalid:
        raise ValueError(f"Attempt to update non-writable columns: {invalid}")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [item_id]
    cursor = db.execute(
        f"UPDATE knowledge_items SET {set_clause} WHERE id = ?", values
    )
    if cursor.rowcount == 0:
        raise KeyError(f"Knowledge item not found: {item_id}")
    if _commit:
        db.commit()


# --- extraction_candidates ---

def create_candidate(
    db: sqlite3.Connection,
    pattern: str,
    guidance: str,
    item_type: str,
    method: str,
    evidence: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    initial_confidence: float = 0.0,
) -> str:
    candidate_id = _new_id()
    db.execute(
        """
        INSERT INTO extraction_candidates
            (id, timestamp, pattern, guidance, type,
             language, runtime, framework, project,
             extraction_method, evidence, initial_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (candidate_id, _now(), pattern, guidance, item_type,
         language, runtime, framework, project,
         method, evidence, initial_confidence),
    )
    db.commit()
    return candidate_id


def promote_candidate(
    db: sqlite3.Connection,
    candidate_id: str,
    source: str = "extraction",
) -> str:
    """Promote an extraction candidate to a knowledge item atomically."""
    row = db.execute(
        "SELECT * FROM extraction_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Candidate not found: {candidate_id}")

    # One atomic transaction: insert item + mark candidate approved.
    with db:
        item_id = _insert_item_row(
            db,
            pattern=row["pattern"],
            guidance=row["guidance"],
            item_type=row["type"],
            language=row["language"],
            runtime=row["runtime"],
            framework=row["framework"],
            project=row["project"],
            base_confidence=row["initial_confidence"],
            source=source,
            evidence=row["evidence"],
        )
        db.execute(
            "UPDATE extraction_candidates SET status = 'approved', merged_into = ? WHERE id = ?",
            (item_id, candidate_id),
        )
    return item_id


# --- archive ---

def archive_item(db: sqlite3.Connection, item_id: str, reason: str) -> None:
    item = get_item(db, item_id)
    with db:
        db.execute(
            "INSERT INTO knowledge_archive (id, archived_at, reason, original_data) VALUES (?, ?, ?, ?)",
            (item_id, _now(), reason, json.dumps(item)),
        )
        db.execute(
            "UPDATE knowledge_items SET status = 'archived' WHERE id = ?", (item_id,)
        )
