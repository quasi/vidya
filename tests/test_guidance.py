"""Tests for guidance.py — contextual guidance generation for MCP responses."""

import os
import tempfile

import pytest

from vidya.schema import init_db
from vidya.store import create_item, create_task, end_task, create_feedback
from vidya.learn import extract_from_feedback


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)


# --- for_start_task ---

class TestForStartTask:

    def test_no_items_suggests_seeding(self, db):
        from vidya.guidance import for_start_task
        g = for_start_task(knowledge=[], project="canon", db=db)
        assert "note" in g
        assert "next_step" in g
        assert "seed" in g["note"].lower() or "no items" in g["note"].lower()

    def test_with_high_items_says_follow(self, db):
        from vidya.guidance import for_start_task
        knowledge = [
            {"id": "a", "effective_confidence": 0.7, "type": "convention",
             "pattern": "Always X", "guidance": "Do X", "fire_count": 5},
            {"id": "b", "effective_confidence": 0.3, "type": "anti_pattern",
             "pattern": "Never Y", "guidance": "Avoid Y", "fire_count": 2},
        ]
        g = for_start_task(knowledge=knowledge, project="canon", db=db)
        assert "1" in g["note"]  # 1 HIGH item
        assert "high" in g["note"].lower() or "HIGH" in g["note"]

    def test_unfired_items_flagged(self, db):
        from vidya.guidance import for_start_task
        knowledge = [
            {"id": "a", "effective_confidence": 0.6, "type": "convention",
             "pattern": "X", "guidance": "Y", "fire_count": 0},
            {"id": "b", "effective_confidence": 0.6, "type": "convention",
             "pattern": "Z", "guidance": "W", "fire_count": 0},
        ]
        g = for_start_task(knowledge=knowledge, project=None, db=db)
        assert "never" in g["note"].lower() or "unfired" in g["note"].lower() or "validated" in g["note"].lower()


# --- for_end_task ---

class TestForEndTask:

    def test_success_outcome(self, db):
        from vidya.guidance import for_end_task
        g = for_end_task(outcome="success", task_id="t1", db=db)
        assert "note" in g
        assert "next_step" in g

    def test_failure_outcome_suggests_feedback(self, db):
        from vidya.guidance import for_end_task
        g = for_end_task(outcome="failure", task_id="t1", db=db)
        assert "feedback" in g["next_step"].lower() or "correction" in g["next_step"].lower()

    def test_repeated_failures_flagged(self, db):
        from vidya.guidance import for_end_task
        # Create tasks with failure outcomes
        t1 = create_task(db, goal="Fix tests", language="python", project="canon")
        end_task(db, t1, outcome="failure")
        t2 = create_task(db, goal="Fix tests again", language="python", project="canon")
        end_task(db, t2, outcome="failure")
        g = for_end_task(outcome="failure", task_id=t2, db=db)
        # Should mention pattern of failures
        note = g["note"].lower()
        assert "failure" in note or "failed" in note


# --- for_feedback ---

class TestForFeedback:

    def test_new_item_created(self, db):
        from vidya.guidance import for_feedback
        learning = {"item_id": "abc123"}
        g = for_feedback(
            feedback_type="user_correction",
            learning=learning,
            db=db,
        )
        assert "0.15" in g["note"] or "low" in g["note"].lower() or "confirmation" in g["next_step"].lower()

    def test_merged_into_existing(self, db):
        from vidya.guidance import for_feedback
        learning = {"merged": True, "item_id": "abc123"}
        g = for_feedback(
            feedback_type="user_correction",
            learning=learning,
            db=db,
        )
        assert "merged" in g["note"].lower() or "existing" in g["note"].lower()

    def test_positive_feedback_no_new_item(self, db):
        from vidya.guidance import for_feedback
        g = for_feedback(
            feedback_type="user_confirmation",
            learning=None,
            db=db,
        )
        assert "note" in g


# --- for_query ---

class TestForQuery:

    def test_no_results(self, db):
        from vidya.guidance import for_query
        g = for_query(items=[], context="error handling", db=db)
        assert "no" in g["note"].lower() or "widen" in g["note"].lower()

    def test_all_medium_items(self, db):
        from vidya.guidance import for_query
        items = [
            {"id": "a", "effective_confidence": 0.35, "type": "convention",
             "pattern": "X", "guidance": "Y"},
            {"id": "b", "effective_confidence": 0.25, "type": "convention",
             "pattern": "Z", "guidance": "W"},
        ]
        g = for_query(items=items, context="testing", db=db)
        note = g["note"].lower()
        assert "medium" in note or "provisional" in note or "suggestion" in note

    def test_high_items_present(self, db):
        from vidya.guidance import for_query
        items = [
            {"id": "a", "effective_confidence": 0.8, "type": "precondition",
             "pattern": "Check X", "guidance": "Always check X"},
        ]
        g = for_query(items=items, context="testing", db=db)
        note = g["note"].lower()
        assert "high" in note or "precondition" in note or "follow" in note


# --- for_explain ---

class TestForExplain:

    def test_never_fired_item(self, db):
        from vidya.guidance import for_explain
        item = {
            "id": "a", "base_confidence": 0.6, "fire_count": 0,
            "success_count": 0, "fail_count": 0,
            "source": "seed", "last_fired": None, "status": "active",
        }
        g = for_explain(item=item, overridden_by=[], db=db)
        assert "never" in g["note"].lower() or "unfired" in g["note"].lower() or "validated" in g["note"].lower()

    def test_item_with_failures(self, db):
        from vidya.guidance import for_explain
        item = {
            "id": "a", "base_confidence": 0.3, "fire_count": 10,
            "success_count": 4, "fail_count": 6,
            "source": "extraction", "last_fired": "2026-03-30T00:00:00+00:00",
            "status": "active",
        }
        g = for_explain(item=item, overridden_by=[], db=db)
        note = g["note"].lower()
        assert "fail" in note or "unreliable" in note or "60%" in note

    def test_overridden_item(self, db):
        from vidya.guidance import for_explain
        item = {
            "id": "a", "base_confidence": 0.6, "fire_count": 5,
            "success_count": 5, "fail_count": 0,
            "source": "seed", "last_fired": "2026-03-30T00:00:00+00:00",
            "status": "active",
        }
        overridden_by = [{"id": "b", "pattern": "Better rule", "guidance": "Do B"}]
        g = for_explain(item=item, overridden_by=overridden_by, db=db)
        assert "overrid" in g["note"].lower()


# --- for_stats ---

class TestForStats:

    def test_empty_db(self, db):
        from vidya.guidance import for_stats
        stats_payload = {
            "total_items": 0, "by_confidence": {"high": 0, "medium": 0, "low": 0},
            "total_tasks": 0, "total_feedback": 0, "total_candidates": 0,
        }
        g = for_stats(stats=stats_payload, db=db)
        assert "empty" in g["note"].lower() or "no items" in g["note"].lower() or "seed" in g["note"].lower()

    def test_healthy_db(self, db):
        from vidya.guidance import for_stats
        stats_payload = {
            "total_items": 22, "by_confidence": {"high": 18, "medium": 3, "low": 1},
            "total_tasks": 10, "total_feedback": 5, "total_candidates": 2,
        }
        g = for_stats(stats=stats_payload, db=db)
        assert "note" in g

    def test_many_low_confidence_flagged(self, db):
        from vidya.guidance import for_stats
        stats_payload = {
            "total_items": 20, "by_confidence": {"high": 2, "medium": 3, "low": 15},
            "total_tasks": 5, "total_feedback": 1, "total_candidates": 0,
        }
        g = for_stats(stats=stats_payload, db=db)
        note = g["note"].lower()
        assert "low" in note or "decay" in note or "stale" in note


# --- for_record_step ---

class TestForRecordStep:

    def test_step_with_matched_items(self, db):
        from vidya.guidance import for_record_step
        matched = [
            {"id": "a", "pattern": "Always check X", "guidance": "Check X before Y"},
        ]
        g = for_record_step(outcome="success", matched_items=matched, db=db)
        assert "note" in g

    def test_error_step_suggests_feedback(self, db):
        from vidya.guidance import for_record_step
        g = for_record_step(outcome="error", matched_items=[], db=db)
        assert "feedback" in g["next_step"].lower() or "correction" in g["next_step"].lower()

    def test_success_step_no_matches(self, db):
        from vidya.guidance import for_record_step
        g = for_record_step(outcome="success", matched_items=[], db=db)
        assert "note" in g
