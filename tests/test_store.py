"""Tests for schema.py and store.py — create/read/update cycle for each table."""

import os
import tempfile

import pytest

from vidya.schema import init_db
from vidya.store import (
    archive_item,
    create_candidate,
    create_feedback,
    create_item,
    create_step,
    create_task,
    end_task,
    get_item,
    promote_candidate,
    update_item,
)


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)


# --- task_records ---

def test_create_task_returns_id(db):
    task_id = create_task(db, goal="Implement error handling", language="python")
    assert task_id is not None
    assert isinstance(task_id, str)


def test_create_task_stores_fields(db):
    task_id = create_task(
        db,
        goal="Implement error handling",
        goal_type="modify",
        language="python",
        project="canon",
    )
    row = db.execute(
        "SELECT goal, goal_type, language, project FROM task_records WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert row["goal"] == "Implement error handling"
    assert row["goal_type"] == "modify"
    assert row["language"] == "python"
    assert row["project"] == "canon"


def test_end_task_sets_outcome(db):
    task_id = create_task(db, goal="Test task", language="python")
    end_task(db, task_id, outcome="success", outcome_detail="All passed")
    row = db.execute(
        "SELECT outcome, outcome_detail FROM task_records WHERE id = ?", (task_id,)
    ).fetchone()
    assert row["outcome"] == "success"
    assert row["outcome_detail"] == "All passed"


# --- step_records ---

def test_create_step_returns_id(db):
    task_id = create_task(db, goal="Test task", language="python")
    step_id = create_step(
        db,
        task_id=task_id,
        action_type="tool_call",
        action_name="read_file",
        result_status="success",
    )
    assert step_id is not None


def test_create_step_links_to_task(db):
    task_id = create_task(db, goal="Test task", language="python")
    step_id = create_step(
        db,
        task_id=task_id,
        action_type="tool_call",
        action_name="read_file",
        result_status="success",
    )
    row = db.execute(
        "SELECT action_name, task_id, sequence FROM step_records WHERE id = ?",
        (step_id,),
    ).fetchone()
    assert row["action_name"] == "read_file"
    assert row["task_id"] == task_id
    assert row["sequence"] == 1


def test_step_sequence_increments(db):
    task_id = create_task(db, goal="Test task", language="python")
    s1 = create_step(db, task_id=task_id, action_type="tool_call", action_name="a", result_status="success")
    s2 = create_step(db, task_id=task_id, action_type="tool_call", action_name="b", result_status="success")
    r1 = db.execute("SELECT sequence FROM step_records WHERE id = ?", (s1,)).fetchone()
    r2 = db.execute("SELECT sequence FROM step_records WHERE id = ?", (s2,)).fetchone()
    assert r1["sequence"] == 1
    assert r2["sequence"] == 2


# --- feedback_records ---

def test_create_feedback_returns_id(db):
    task_id = create_task(db, goal="Test task", language="python")
    feedback_id = create_feedback(
        db,
        feedback_type="review_rejected",
        detail="Never use raw SQL",
        language="python",
        task_id=task_id,
    )
    assert feedback_id is not None


def test_create_feedback_stores_fields(db):
    feedback_id = create_feedback(
        db,
        feedback_type="review_rejected",
        detail="Never use raw SQL",
        language="python",
    )
    row = db.execute(
        "SELECT feedback_type, detail, language FROM feedback_records WHERE id = ?",
        (feedback_id,),
    ).fetchone()
    assert row["feedback_type"] == "review_rejected"
    assert row["detail"] == "Never use raw SQL"
    assert row["language"] == "python"


# --- knowledge_items ---

def test_create_item_returns_id(db):
    item_id = create_item(
        db,
        pattern="error handling",
        guidance="Use Result types",
        item_type="convention",
        language="python",
    )
    assert item_id is not None


def test_get_item_returns_fields(db):
    item_id = create_item(
        db,
        pattern="error handling",
        guidance="Use Result types",
        item_type="convention",
        language="python",
        base_confidence=0.5,
    )
    item = get_item(db, item_id)
    assert item["pattern"] == "error handling"
    assert item["guidance"] == "Use Result types"
    assert item["type"] == "convention"
    assert item["language"] == "python"
    assert item["base_confidence"] == pytest.approx(0.5)
    assert item["status"] == "active"


def test_update_item_changes_field(db):
    item_id = create_item(
        db, pattern="error handling", guidance="Use Result types", item_type="convention", language="python", base_confidence=0.5
    )
    update_item(db, item_id, base_confidence=0.7)
    item = get_item(db, item_id)
    assert item["base_confidence"] == pytest.approx(0.7)


def test_update_item_multiple_fields(db):
    item_id = create_item(
        db, pattern="testing", guidance="Use pytest", item_type="convention", language="python"
    )
    update_item(db, item_id, base_confidence=0.8, fire_count=5, success_count=5)
    item = get_item(db, item_id)
    assert item["base_confidence"] == pytest.approx(0.8)
    assert item["fire_count"] == 5
    assert item["success_count"] == 5


# --- extraction_candidates ---

def test_create_candidate_returns_id(db):
    candidate_id = create_candidate(
        db,
        pattern="error handling",
        guidance="Always use Result types",
        item_type="convention",
        language="python",
        method="feedback",
        evidence='["fb_123"]',
        initial_confidence=0.15,
    )
    assert candidate_id is not None


def test_promote_candidate_creates_item(db):
    candidate_id = create_candidate(
        db,
        pattern="error handling",
        guidance="Always use Result types",
        item_type="convention",
        language="python",
        method="feedback",
        evidence='["fb_123"]',
        initial_confidence=0.15,
    )
    item_id = promote_candidate(db, candidate_id)
    item = get_item(db, item_id)
    assert item["pattern"] == "error handling"
    assert item["guidance"] == "Always use Result types"
    assert item["base_confidence"] == pytest.approx(0.15)


def test_promote_candidate_marks_approved(db):
    candidate_id = create_candidate(
        db,
        pattern="error handling",
        guidance="Always use Result types",
        item_type="convention",
        language="python",
        method="feedback",
        evidence='["fb_123"]',
        initial_confidence=0.15,
    )
    item_id = promote_candidate(db, candidate_id)
    row = db.execute(
        "SELECT status, merged_into FROM extraction_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert row["status"] == "approved"
    assert row["merged_into"] == item_id


# --- archive ---

def test_archive_item_sets_status(db):
    item_id = create_item(
        db, pattern="old pattern", guidance="Old guidance", item_type="convention", language="python"
    )
    archive_item(db, item_id, reason="manual")
    item = get_item(db, item_id)
    assert item["status"] == "archived"


def test_archive_item_writes_to_archive_table(db):
    item_id = create_item(
        db, pattern="old pattern", guidance="Old guidance", item_type="convention", language="python"
    )
    archive_item(db, item_id, reason="manual")
    row = db.execute(
        "SELECT reason FROM knowledge_archive WHERE id = ?", (item_id,)
    ).fetchone()
    assert row is not None
    assert row["reason"] == "manual"


def test_update_item_rejects_invalid_column(db):
    item_id = create_item(
        db, pattern="p", guidance="g", item_type="convention", language="python"
    )
    with pytest.raises(ValueError, match="non-writable"):
        update_item(db, item_id, id="injected")


def test_end_task_raises_on_missing_id(db):
    with pytest.raises(KeyError):
        end_task(db, "nonexistent-id", outcome="success")


# --- WAL + FK ---

def test_wal_mode_enabled(db):
    row = db.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_foreign_keys_enabled(db):
    row = db.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1
