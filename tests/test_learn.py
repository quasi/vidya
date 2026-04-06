"""Tests for learn.py — feedback-driven extraction engine."""

import pytest

from vidya.confidence import SOURCE_CONFIDENCE
from vidya.store import create_item, create_feedback, get_item
from vidya.learn import extract_from_feedback


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
    assert item["base_confidence"] == pytest.approx(SOURCE_CONFIDENCE["review_rejected"])


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


def test_test_passed_does_not_create_or_update_items(db):
    """test_passed is recorded but does NOT affect knowledge items."""
    item_id = create_item(db, pattern="error handling", guidance="Handle errors",
                          item_type="convention", language="python", base_confidence=0.5)
    feedback = _feedback(db, "test_passed", "error handling test passed", language="python")
    result = extract_from_feedback(db, feedback)
    assert result is None
    item = get_item(db, item_id)
    assert item["base_confidence"] == 0.5
    assert item["fire_count"] == 0


# --- Two-tier promotion and source-based confidence ---

def test_correction_creates_at_high_confidence(db):
    """User corrections should create items at 0.85, not 0.15."""
    feedback = _feedback(db, "user_correction", "Use uv sync not pip install",
                         language="python", project="vidya")
    result = extract_from_feedback(db, feedback)
    assert result is not None
    item = get_item(db, result["item_id"])
    assert item["base_confidence"] == pytest.approx(SOURCE_CONFIDENCE["user_correction"])
    assert item["source"] == "user_correction"


def test_review_rejected_creates_at_lower_confidence(db):
    """review_rejected items get 0.65, not 0.85."""
    feedback = _feedback(db, "review_rejected", "Avoid global mutable state",
                         language="python")
    result = extract_from_feedback(db, feedback)
    item = get_item(db, result["item_id"])
    assert item["base_confidence"] == pytest.approx(SOURCE_CONFIDENCE["review_rejected"])
    assert item["source"] == "review_rejected"


def test_unmatched_confirmation_creates_candidate_not_item(db):
    """Unmatched confirmations create pending candidates, not active items."""
    feedback = _feedback(db, "user_confirmation", "Always run linting before commit",
                         language="python", project="vidya")
    result = extract_from_feedback(db, feedback)
    assert result is not None
    assert "candidate_id" in result
    assert "item_id" not in result
    candidate = db.execute("SELECT * FROM extraction_candidates WHERE id = ?",
                           (result["candidate_id"],)).fetchone()
    assert candidate is not None
    assert candidate["status"] == "pending"
    assert candidate["initial_confidence"] == pytest.approx(SOURCE_CONFIDENCE["user_confirmation"])


def test_unmatched_failure_creates_diagnostic_candidate(db):
    """Test failure with no match creates a diagnostic candidate."""
    feedback = _feedback(db, "test_failed", "Integration test fails on empty database",
                         language="python", project="vidya")
    result = extract_from_feedback(db, feedback)
    assert result is not None
    assert "candidate_id" in result
    candidate = db.execute("SELECT * FROM extraction_candidates WHERE id = ?",
                           (result["candidate_id"],)).fetchone()
    assert candidate["type"] == "diagnostic"
    assert candidate["initial_confidence"] == pytest.approx(SOURCE_CONFIDENCE["test_outcome"])


def test_matched_confirmation_boosts_no_new_item(db):
    """Confirmation matching an existing item should boost it, not create a new one."""
    create_item(db, pattern="run linting", guidance="Always run linting before commit",
                item_type="convention", language="python",
                base_confidence=0.5, source="extraction")
    feedback = _feedback(db, "user_confirmation", "Always run linting before commit",
                         language="python")
    result = extract_from_feedback(db, feedback)
    assert result is None


def test_no_feedback_signal_is_dropped(db):
    """Every feedback type should produce a result when no matches exist."""
    import uuid
    for fb_type in ["user_correction", "user_confirmation", "test_failed", "review_rejected"]:
        # Use a fully random UUID so there is zero token overlap between iterations.
        unique_key = uuid.uuid4().hex
        feedback = _feedback(db, fb_type,
                             f"{fb_type}_{unique_key}",
                             language="python", project="vidya")
        result = extract_from_feedback(db, feedback)
        assert result is not None, f"{fb_type} was silently dropped"
