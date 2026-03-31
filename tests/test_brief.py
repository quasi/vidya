"""Tests for brief.py — structured context dump for vidya_brief MCP tool."""

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


class TestAssembleBrief:

    def test_empty_db_returns_valid_structure(self, db):
        from vidya.brief import assemble_brief
        result = assemble_brief(db)
        assert "project_state" in result
        assert "attention_items" in result
        assert "input_quality_hints" in result
        assert result["project_state"]["total_items"] == 0

    def test_project_state_counts(self, db):
        from vidya.brief import assemble_brief
        create_item(db, pattern="X", guidance="Y", item_type="convention",
                    language="python", project="canon", base_confidence=0.7)
        create_item(db, pattern="A", guidance="B", item_type="anti_pattern",
                    language="python", project="canon", base_confidence=0.3)
        result = assemble_brief(db, language="python", project="canon")
        state = result["project_state"]
        assert state["total_items"] == 2
        assert state["high"] >= 1
        assert state["by_type"]["convention"] == 1
        assert state["by_type"]["anti_pattern"] == 1

    def test_never_fired_items_in_attention(self, db):
        from vidya.brief import assemble_brief
        create_item(db, pattern="Unfired", guidance="Never used",
                    item_type="convention", language="python",
                    base_confidence=0.6)
        result = assemble_brief(db, language="python")
        attention = result["attention_items"]
        unfired = [a for a in attention if "never" in a["reason"].lower() or "unfired" in a["reason"].lower()]
        assert len(unfired) >= 1

    def test_high_failure_items_in_attention(self, db):
        from vidya.brief import assemble_brief
        item_id = create_item(db, pattern="Flaky", guidance="Unreliable rule",
                              item_type="convention", language="python",
                              base_confidence=0.3)
        from vidya.store import update_item
        update_item(db, item_id, fire_count=10, fail_count=7, success_count=3,
                    last_fired="2026-03-30T00:00:00+00:00")
        result = assemble_brief(db, language="python")
        attention = result["attention_items"]
        flaky = [a for a in attention if "fail" in a["reason"].lower()]
        assert len(flaky) >= 1

    def test_last_task_outcome_included(self, db):
        from vidya.brief import assemble_brief
        t1 = create_task(db, goal="Do something", language="python")
        end_task(db, t1, outcome="failure", failure_type="wrong_result")
        result = assemble_brief(db, language="python")
        assert result["project_state"]["last_task_outcome"] == "failure"

    def test_no_tasks_last_outcome_none(self, db):
        from vidya.brief import assemble_brief
        result = assemble_brief(db)
        assert result["project_state"]["last_task_outcome"] is None

    def test_input_quality_hints_present(self, db):
        from vidya.brief import assemble_brief
        result = assemble_brief(db)
        hints = result["input_quality_hints"]
        assert "context" in hints
        assert "feedback_detail" in hints
        # Hints should contain actual guidance, not empty strings
        assert len(hints["context"]) > 20
        assert len(hints["feedback_detail"]) > 20

    def test_scope_filtering(self, db):
        from vidya.brief import assemble_brief
        create_item(db, pattern="Python rule", guidance="Py",
                    item_type="convention", language="python",
                    base_confidence=0.6)
        create_item(db, pattern="CL rule", guidance="CL",
                    item_type="convention", language="common-lisp",
                    base_confidence=0.6)
        result = assemble_brief(db, language="python")
        assert result["project_state"]["total_items"] == 1

    def test_recent_feedback_count(self, db):
        from vidya.brief import assemble_brief
        create_feedback(db, feedback_type="user_correction",
                        detail="Use X not Y", language="python")
        create_feedback(db, feedback_type="user_confirmation",
                        detail="X is correct", language="python")
        result = assemble_brief(db, language="python")
        assert result["project_state"]["total_feedback"] == 2
