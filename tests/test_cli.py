"""Tests for cli.py — new commands: task start/end, step, brief; --json flag."""

import json
import pytest
from click.testing import CliRunner

from vidya.cli import main
from vidya.store import create_item


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli(db, monkeypatch):
    """CliRunner with _db() monkeypatched to test database."""
    monkeypatch.setattr("vidya.cli._db", lambda: db)
    return CliRunner()


# --- task start ---

def test_task_start_creates_task(cli):
    result = cli.invoke(main, ["task", "start", "--goal", "fix error handling"])
    assert result.exit_code == 0, result.output
    assert "Task:" in result.output


def test_task_start_json_returns_task_id(cli):
    result = cli.invoke(main, ["--json", "task", "start", "--goal", "fix error handling"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "task_id" in data
    assert isinstance(data["task_id"], str)
    assert len(data["task_id"]) > 0
    assert "_guidance" in data
    assert "note" in data["_guidance"]
    assert "next_step" in data["_guidance"]


def test_task_start_json_includes_knowledge(cli, db):
    create_item(db, pattern="error handling", guidance="Use specific exceptions",
                item_type="convention", language="python", base_confidence=0.7)
    result = cli.invoke(main, ["--json", "task", "start", "--goal", "error handling",
                                "--language", "python"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "knowledge" in data
    assert isinstance(data["knowledge"], list)


def test_task_start_with_language_and_project(cli):
    result = cli.invoke(main, ["task", "start", "--goal", "deploy app",
                                "--language", "python", "--project", "vidya"])
    assert result.exit_code == 0, result.output
    assert "Task:" in result.output


def test_task_start_goal_required(cli):
    result = cli.invoke(main, ["task", "start"])
    assert result.exit_code != 0


# --- task end ---

def test_task_end_records_completion(cli):
    # First create a task
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "test goal"])
    task_id = json.loads(create_result.output)["task_id"]

    result = cli.invoke(main, ["task", "end", "--task-id", task_id, "--outcome", "success"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output.lower() or "success" in result.output.lower()


def test_task_end_json(cli):
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "test goal"])
    task_id = json.loads(create_result.output)["task_id"]

    result = cli.invoke(main, ["--json", "task", "end", "--task-id", task_id,
                                "--outcome", "success"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data.get("ok") is True
    assert "_guidance" in data
    assert "note" in data["_guidance"]
    assert "next_step" in data["_guidance"]


def test_task_end_failure_outcome(cli):
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "test goal"])
    task_id = json.loads(create_result.output)["task_id"]

    result = cli.invoke(main, ["task", "end", "--task-id", task_id,
                                "--outcome", "failure", "--detail", "tests did not pass"])
    assert result.exit_code == 0, result.output


def test_task_end_invalid_outcome(cli):
    result = cli.invoke(main, ["task", "end", "--task-id", "x", "--outcome", "oops"])
    assert result.exit_code != 0


# --- step ---

def test_step_records_step(cli):
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "fix bug"])
    task_id = json.loads(create_result.output)["task_id"]

    result = cli.invoke(main, ["step", "--task-id", task_id,
                                "--action", "run pytest",
                                "--result", "3 tests failed",
                                "--outcome", "error"])
    assert result.exit_code == 0, result.output
    assert "step" in result.output.lower()


def test_step_json_returns_step_id(cli):
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "fix bug"])
    task_id = json.loads(create_result.output)["task_id"]

    result = cli.invoke(main, ["--json", "step", "--task-id", task_id,
                                "--action", "run pytest",
                                "--result", "all passed",
                                "--outcome", "success"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "step_id" in data
    assert isinstance(data["step_id"], str)
    assert "_guidance" in data
    assert "note" in data["_guidance"]
    assert "next_step" in data["_guidance"]


def test_step_with_rationale(cli):
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "fix bug"])
    task_id = json.loads(create_result.output)["task_id"]

    result = cli.invoke(main, ["step", "--task-id", task_id,
                                "--action", "add type hints",
                                "--result", "mypy happy",
                                "--outcome", "success",
                                "--rationale", "PEP 484 compliance"])
    assert result.exit_code == 0, result.output


def test_step_outcome_choices(cli):
    create_result = cli.invoke(main, ["--json", "task", "start", "--goal", "x"])
    task_id = json.loads(create_result.output)["task_id"]
    result = cli.invoke(main, ["step", "--task-id", task_id,
                                "--action", "x", "--result", "x", "--outcome", "bad_value"])
    assert result.exit_code != 0


# --- brief ---

def test_brief_human_output(cli):
    result = cli.invoke(main, ["brief"])
    assert result.exit_code == 0, result.output
    # Should contain item counts
    assert "Items:" in result.output or "items" in result.output.lower()


def test_brief_json_structure(cli):
    result = cli.invoke(main, ["--json", "brief"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "project_state" in data
    assert "attention_items" in data
    assert "input_quality_hints" in data


def test_brief_json_with_items(cli, db):
    create_item(db, pattern="always use type hints", guidance="Use type hints",
                item_type="convention", language="python", base_confidence=0.7)
    result = cli.invoke(main, ["--json", "brief", "--language", "python"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["project_state"]["total_items"] == 1


def test_brief_with_language_and_project(cli):
    result = cli.invoke(main, ["brief", "--language", "python", "--project", "vidya"])
    assert result.exit_code == 0, result.output


# --- --json flag on existing commands ---

def test_query_json(cli, db):
    create_item(db, pattern="error handling", guidance="Use exceptions",
                item_type="convention", language="python", base_confidence=0.7)
    result = cli.invoke(main, ["--json", "query", "--context", "error handling",
                                "--language", "python"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "items" in data
    assert "guidance" in data["items"][0]
    assert "effective_confidence" in data["items"][0]
    assert "_guidance" in data
    assert "note" in data["_guidance"]
    assert "next_step" in data["_guidance"]


def test_query_json_empty(cli):
    result = cli.invoke(main, ["--json", "query", "--context", "nothing here"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["items"] == []
    assert "_guidance" in data


def test_stats_json(cli, db):
    create_item(db, pattern="x", guidance="y", item_type="convention",
                language="python", base_confidence=0.7)
    result = cli.invoke(main, ["--json", "stats"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "total_items" in data
    assert data["total_items"] >= 1
    assert "_guidance" in data
    assert "note" in data["_guidance"]


def test_feedback_json(cli):
    result = cli.invoke(main, ["--json", "feedback",
                                "--type", "user_correction",
                                "--detail", "Always use type hints",
                                "--language", "python"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "item_id" in data or "updated" in data
    assert "_guidance" in data
    assert "note" in data["_guidance"]


def test_existing_query_human_still_works(cli, db):
    """Regression: human-readable query output unchanged when --json not passed."""
    create_item(db, pattern="error handling", guidance="Use exceptions",
                item_type="convention", language="python", base_confidence=0.7)
    result = cli.invoke(main, ["query", "--context", "error handling", "--language", "python"])
    assert result.exit_code == 0, result.output
    assert "HIGH" in result.output or "MED" in result.output
    assert "Guidance:" in result.output


# --- maintain ---

def test_maintain_shows_health(cli):
    result = cli.invoke(main, ["maintain"])
    assert result.exit_code == 0, result.output
    assert "Health:" in result.output


def test_maintain_json_includes_health(cli, db):
    create_item(db, pattern="test", guidance="test", item_type="convention",
                base_confidence=0.5, source="seed")
    result = cli.invoke(main, ["--json", "maintain"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "health" in data
    assert "_guidance" in data


def test_maintain_archive_flag_dry_run_by_default(cli, db):
    create_item(db, pattern="weak", guidance="X", item_type="convention",
                base_confidence=0.05, source="extraction")
    result = cli.invoke(main, ["--json", "maintain", "--archive"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["archive"]["would_archive_count"] >= 1
    assert data["archive"]["archived_count"] == 0


def test_maintain_archive_confirm_actually_archives(cli, db):
    create_item(db, pattern="weak", guidance="X", item_type="convention",
                base_confidence=0.05, source="extraction")
    result = cli.invoke(main, ["--json", "maintain", "--archive", "--confirm"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["archive"]["archived_count"] >= 1


# --- step action types ---

def test_step_accepts_action_type(cli, db):
    from vidya.store import create_task
    task_id = create_task(db, goal="test", language="python")
    result = cli.invoke(main, [
        "step", "--task-id", task_id,
        "--action", "Read the config file",
        "--result", "Found the setting",
        "--outcome", "success",
        "--action-type", "discovery",
    ])
    assert result.exit_code == 0, result.output


def test_step_defaults_action_type_to_decision(cli, db):
    from vidya.store import create_task
    task_id = create_task(db, goal="test", language="python")
    result = cli.invoke(main, [
        "step", "--task-id", task_id,
        "--action", "Chose approach A",
        "--result", "It worked",
        "--outcome", "success",
    ])
    assert result.exit_code == 0, result.output
    row = db.execute(
        "SELECT action_type FROM step_records WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert row["action_type"] == "decision"


def test_step_rejects_invalid_action_type(cli, db):
    from vidya.store import create_task
    task_id = create_task(db, goal="test", language="python")
    result = cli.invoke(main, [
        "step", "--task-id", task_id,
        "--action", "test",
        "--result", "test",
        "--outcome", "success",
        "--action-type", "nonsense",
    ])
    assert result.exit_code != 0
