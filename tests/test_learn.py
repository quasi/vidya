"""Tests for learn.py — feedback-driven extraction engine."""

import os
import tempfile

import pytest

from vidya.schema import init_db
from vidya.store import create_item, create_feedback, get_item
from vidya.learn import extract_from_feedback


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)


def _feedback(db, feedback_type: str, detail: str, language: str = "python", **kwargs):
    """Helper: create a feedback record and return its id + dict."""
    fid = create_feedback(db, feedback_type=feedback_type, detail=detail,
                          language=language, **kwargs)
    row = db.execute("SELECT * FROM feedback_records WHERE id = ?", (fid,)).fetchone()
    return dict(row)


# --- Test 1: review_rejected creates a new knowledge item ---

def test_review_rejected_creates_knowledge_item(db):
    feedback = _feedback(db, "review_rejected", "Never use raw SQL — always use parameterized queries",
                         language="python")
    result = extract_from_feedback(db, feedback)
    assert result is not None
    # The item should be in knowledge_items
    item = get_item(db, result["item_id"])
    assert item["guidance"] == "Never use raw SQL — always use parameterized queries"
    assert item["language"] == "python"
    assert item["status"] == "active"


def test_review_rejected_sets_low_confidence(db):
    feedback = _feedback(db, "review_rejected", "Never use raw SQL", language="python")
    result = extract_from_feedback(db, feedback)
    item = get_item(db, result["item_id"])
    assert item["base_confidence"] == pytest.approx(0.15)


def test_user_correction_also_creates_item(db):
    feedback = _feedback(db, "user_correction", "Always use type hints on public APIs",
                         language="python")
    result = extract_from_feedback(db, feedback)
    assert result is not None
    item = get_item(db, result["item_id"])
    assert "type hints" in item["guidance"]


# --- Test 2: second rejection on same topic merges, doesn't duplicate ---

def test_second_rejection_on_same_topic_merges_evidence(db):
    fb1 = _feedback(db, "review_rejected", "Never use raw SQL — always parameterize",
                    language="python")
    result1 = extract_from_feedback(db, fb1)
    assert result1 is not None
    item_id = result1["item_id"]

    # Very similar feedback
    fb2 = _feedback(db, "review_rejected", "Never raw SQL — use parameterized queries always",
                    language="python")
    result2 = extract_from_feedback(db, fb2)

    # No new item should have been created
    assert result2 is None or result2.get("merged", False)

    # Still only one item on this topic
    count = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE language = 'python' AND guidance LIKE '%SQL%'"
    ).fetchone()[0]
    assert count == 1


# --- Test 3: review_accepted boosts confidence on matching item ---

def test_review_accepted_boosts_matching_item(db):
    item_id = create_item(
        db, pattern="parameterized queries", guidance="Always use parameterized queries",
        item_type="convention", language="python", base_confidence=0.4
    )
    feedback = _feedback(db, "review_accepted", "Always use parameterized queries",
                         language="python")
    result = extract_from_feedback(db, feedback)

    item = get_item(db, item_id)
    # base_confidence should have increased
    assert item["base_confidence"] > 0.4


def test_user_confirmation_also_boosts(db):
    item_id = create_item(
        db, pattern="type hints", guidance="Always use type hints",
        item_type="convention", language="python", base_confidence=0.5
    )
    feedback = _feedback(db, "user_confirmation", "Always use type hints", language="python")
    extract_from_feedback(db, feedback)

    item = get_item(db, item_id)
    assert item["base_confidence"] > 0.5


# --- Test 4: test_failed decreases confidence on matching item ---

def test_test_failed_decreases_confidence(db):
    item_id = create_item(
        db, pattern="database migrations", guidance="Run migrations before deploying",
        item_type="precondition", language="python", base_confidence=0.7
    )
    feedback = _feedback(db, "test_failed", "Run migrations before deploying",
                         language="python")
    extract_from_feedback(db, feedback)

    item = get_item(db, item_id)
    assert item["base_confidence"] < 0.7


# --- Type classification heuristics ---

def test_never_keyword_classifies_as_anti_pattern(db):
    feedback = _feedback(db, "review_rejected", "Never use mutable default arguments",
                         language="python")
    result = extract_from_feedback(db, feedback)
    item = get_item(db, result["item_id"])
    assert item["type"] == "anti_pattern"


def test_always_keyword_classifies_as_convention(db):
    feedback = _feedback(db, "review_rejected", "Always add type hints to public functions",
                         language="python")
    result = extract_from_feedback(db, feedback)
    item = get_item(db, result["item_id"])
    assert item["type"] == "convention"


def test_before_keyword_classifies_as_precondition(db):
    feedback = _feedback(db, "review_rejected", "Before deploying, ensure tests pass",
                         language="python")
    result = extract_from_feedback(db, feedback)
    item = get_item(db, result["item_id"])
    assert item["type"] == "precondition"


def test_default_classification_is_convention(db):
    feedback = _feedback(db, "review_rejected", "Use context managers for file operations",
                         language="python")
    result = extract_from_feedback(db, feedback)
    item = get_item(db, result["item_id"])
    assert item["type"] == "convention"
