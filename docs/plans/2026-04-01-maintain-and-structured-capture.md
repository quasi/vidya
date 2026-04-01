# Maintain Command + Structured Data Capture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `vidya maintain` for knowledge base hygiene (stale item detection, health reporting, optional auto-archive) and improve data capture quality by introducing structured action types for step records, so future frequency extraction has clusterable data.

**Architecture:** Two independent workstreams sharing no code. Workstream A extends `maintain.py` with stale-item surfacing and archive-on-threshold logic, plus a new `maintain` CLI subcommand. Workstream C adds a controlled vocabulary for `action_type` in step records (validated in `store.py`), updates the `step` CLI command to accept it, and updates the `using-vidya` skill doc. Both are pure additions — no existing behavior changes.

**Tech Stack:** Python 3.12, SQLite, Click, pytest. No new dependencies.

---

## Workstream A: `vidya maintain`

### Task 1: Stale item detection in maintain.py

**Files:**
- Modify: `src/vidya/maintain.py`
- Test: `tests/test_maintain.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_maintain.py`. We need tests for the new `find_stale_items` function:

```python
"""Tests for maintain.py — stale item detection and health report."""

import pytest
from datetime import datetime, timezone, timedelta

from vidya.store import create_item, update_item
from vidya.maintain import compute_stats, find_stale_items


def test_find_stale_items_returns_empty_for_fresh_items(db):
    """Items created today are not stale."""
    create_item(db, pattern="test", guidance="test", item_type="convention",
                base_confidence=0.5, source="seed")
    stale = find_stale_items(db)
    assert stale == []


def test_find_stale_items_finds_unfired_old_items(db):
    """Items with first_seen > 90 days ago and never fired are stale."""
    item_id = create_item(db, pattern="old rule", guidance="do X",
                          item_type="convention", base_confidence=0.3, source="seed")
    # Backdate first_seen to 100 days ago
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id
    assert "unfired" in stale[0]["reason"].lower() or "never fired" in stale[0]["reason"].lower()


def test_find_stale_items_finds_long_unfired_items(db):
    """Items last fired > 90 days ago are stale."""
    item_id = create_item(db, pattern="stale rule", guidance="do Y",
                          item_type="convention", base_confidence=0.4, source="seed")
    old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    update_item(db, item_id, last_fired=old_date, fire_count=5)
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id


def test_find_stale_items_finds_low_confidence_items(db):
    """Items with effective confidence below threshold are stale."""
    item_id = create_item(db, pattern="weak rule", guidance="maybe do Z",
                          item_type="convention", base_confidence=0.05, source="extraction")
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id


def test_find_stale_items_respects_scope_filter(db):
    """Language/project filter narrows stale item search."""
    id1 = create_item(db, pattern="python rule", guidance="X", item_type="convention",
                      language="python", base_confidence=0.05, source="extraction")
    id2 = create_item(db, pattern="go rule", guidance="Y", item_type="convention",
                      language="go", base_confidence=0.05, source="extraction")
    stale = find_stale_items(db, language="python")
    ids = [s["id"] for s in stale]
    assert id1 in ids
    assert id2 not in ids


def test_find_stale_items_skips_archived(db):
    """Archived items are not reported as stale."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.05, source="extraction")
    update_item(db, item_id, status="archived")
    stale = find_stale_items(db)
    assert stale == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_maintain.py -v`
Expected: FAIL — `find_stale_items` does not exist.

**Step 3: Implement `find_stale_items`**

Add to `src/vidya/maintain.py`:

```python
def find_stale_items(
    db: sqlite3.Connection,
    language: str | None = None,
    project: str | None = None,
    stale_days: int = 90,
    min_confidence: float = 0.2,
) -> list[dict[str, Any]]:
    """Find items that are stale: unfired for too long, or below confidence threshold.

    Returns list of dicts with keys: id, pattern, guidance, reason, effective_confidence, days_since_activity.
    """
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
```

Also add the `Any` import at the top of maintain.py:
```python
from typing import Any
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_maintain.py -v`
Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
git add tests/test_maintain.py src/vidya/maintain.py
git commit -m "feat(maintain): add find_stale_items — surfaces unfired and low-confidence items"
```

---

### Task 2: Health report in maintain.py

**Files:**
- Modify: `src/vidya/maintain.py`
- Modify: `tests/test_maintain.py`

**Step 1: Write the failing test**

Append to `tests/test_maintain.py`:

```python
from vidya.maintain import health_report


def test_health_report_empty_db(db):
    report = health_report(db)
    assert report["total_items"] == 0
    assert report["stale_count"] == 0
    assert report["health"] == "empty"


def test_health_report_healthy_db(db):
    for i in range(5):
        create_item(db, pattern=f"rule {i}", guidance=f"do {i}",
                    item_type="convention", base_confidence=0.6, source="seed")
    report = health_report(db)
    assert report["total_items"] == 5
    assert report["stale_count"] == 0
    assert report["health"] == "healthy"


def test_health_report_degraded_when_many_stale(db):
    """More than 50% stale items → degraded."""
    # 3 low-confidence items (stale)
    for i in range(3):
        create_item(db, pattern=f"weak {i}", guidance=f"X {i}",
                    item_type="convention", base_confidence=0.05, source="extraction")
    # 1 healthy item
    create_item(db, pattern="strong", guidance="Y",
                item_type="convention", base_confidence=0.8, source="seed")
    report = health_report(db)
    assert report["health"] == "degraded"
    assert report["stale_count"] == 3
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_maintain.py::test_health_report_empty_db -v`
Expected: FAIL — `health_report` does not exist.

**Step 3: Implement `health_report`**

Add to `src/vidya/maintain.py`:

```python
def health_report(
    db: sqlite3.Connection,
    language: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Compute a health report for the knowledge base.

    Returns dict with: total_items, stale_count, stale_items, health ('empty'|'healthy'|'degraded').
    """
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_maintain.py -v`
Expected: All 9 tests PASS.

**Step 5: Commit**

```bash
git add src/vidya/maintain.py tests/test_maintain.py
git commit -m "feat(maintain): add health_report — health status with stale item breakdown"
```

---

### Task 3: Auto-archive function

**Files:**
- Modify: `src/vidya/maintain.py`
- Modify: `tests/test_maintain.py`

**Step 1: Write the failing test**

Append to `tests/test_maintain.py`:

```python
from vidya.maintain import auto_archive_stale
from vidya.store import get_item


def test_auto_archive_archives_stale_items(db):
    """Items below archive threshold get archived."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.05, source="extraction")
    result = auto_archive_stale(db, dry_run=False)
    assert result["archived_count"] == 1
    assert item_id in result["archived_ids"]
    item = get_item(db, item_id)
    assert item["status"] == "archived"


def test_auto_archive_dry_run_does_not_archive(db):
    """Dry run reports but does not archive."""
    create_item(db, pattern="dead rule", guidance="X",
                item_type="convention", base_confidence=0.05, source="extraction")
    result = auto_archive_stale(db, dry_run=True)
    assert result["archived_count"] == 0
    assert result["would_archive_count"] == 1


def test_auto_archive_skips_items_above_threshold(db):
    """Healthy items are not archived."""
    create_item(db, pattern="good rule", guidance="X",
                item_type="convention", base_confidence=0.6, source="seed")
    result = auto_archive_stale(db, dry_run=False)
    assert result["archived_count"] == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_maintain.py::test_auto_archive_archives_stale_items -v`
Expected: FAIL — `auto_archive_stale` does not exist.

**Step 3: Implement `auto_archive_stale`**

Add to `src/vidya/maintain.py`:

```python
from vidya.store import archive_item


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

    Returns dict with: archived_count, archived_ids, would_archive_count.
    """
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_maintain.py -v`
Expected: All 12 tests PASS.

**Step 5: Commit**

```bash
git add src/vidya/maintain.py tests/test_maintain.py
git commit -m "feat(maintain): add auto_archive_stale — dry-run-safe archival of low-confidence items"
```

---

### Task 4: `vidya maintain` CLI subcommand

**Files:**
- Modify: `src/vidya/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `src/vidya/guidance.py`

**Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_cli.py::test_maintain_shows_health -v`
Expected: FAIL — no `maintain` command.

**Step 3: Add guidance function for maintain**

Add to `src/vidya/guidance.py`:

```python
def for_maintain(
    health: str,
    stale_count: int,
    archive_result: dict[str, Any] | None,
    db: sqlite3.Connection,
) -> dict[str, str]:
    if health == "empty":
        return {
            "note": "Knowledge base is empty.",
            "next_step": "Seed knowledge with vidya seed before running maintenance.",
        }

    parts = [f"Health: {health}."]
    if stale_count > 0:
        parts.append(f"{stale_count} stale item{'s' if stale_count != 1 else ''} found.")

    if archive_result:
        if archive_result.get("archived_count", 0) > 0:
            parts.append(f"Archived {archive_result['archived_count']} item(s).")
        elif archive_result.get("would_archive_count", 0) > 0:
            parts.append(f"{archive_result['would_archive_count']} item(s) would be archived. Use --confirm to execute.")

    if health == "degraded":
        next_step = "Review stale items with vidya items --min-confidence 0. Confirm or archive them."
    elif stale_count > 0:
        next_step = "Run vidya maintain --archive --confirm to clean up, or review items individually."
    else:
        next_step = "No maintenance needed. Continue working."

    return {"note": " ".join(parts), "next_step": next_step}
```

**Step 4: Implement the `maintain` CLI subcommand**

Add to `src/vidya/cli.py`:

Import additions at top:
```python
from vidya.maintain import compute_stats, health_report, auto_archive_stale
from vidya.guidance import (
    for_start_task, for_end_task, for_record_step,
    for_query, for_feedback, for_explain, for_stats, for_maintain,
)
```

New command:
```python
@main.command()
@click.option("--language", default=None)
@click.option("--project", default=None)
@click.option("--archive", is_flag=True, default=False,
              help="Include archive recommendations for stale items.")
@click.option("--confirm", is_flag=True, default=False,
              help="Actually archive stale items (requires --archive).")
@click.pass_context
def maintain(ctx, language, project, archive, confirm):
    """Run maintenance: health check, stale item detection, optional archival."""
    db = _db()
    report = health_report(db, language=language, project=project)

    archive_result = None
    if archive:
        archive_result = auto_archive_stale(
            db, language=language, project=project, dry_run=not confirm,
        )

    if ctx.obj.get("json"):
        payload = {
            "health": report["health"],
            "total_items": report["total_items"],
            "by_confidence": report["by_confidence"],
            "stale_count": report["stale_count"],
            "stale_items": report["stale_items"],
        }
        if archive_result is not None:
            payload["archive"] = archive_result
        payload["_guidance"] = for_maintain(
            health=report["health"],
            stale_count=report["stale_count"],
            archive_result=archive_result,
            db=db,
        )
        click.echo(json.dumps(payload))
        return

    click.echo(f"Health: {report['health']}")
    click.echo(f"Items:  {report['total_items']} (HIGH={report['by_confidence']['high']} "
               f"MED={report['by_confidence']['medium']} LOW={report['by_confidence']['low']})")
    click.echo(f"Tasks:  {report['total_tasks']}  Feedback: {report['total_feedback']}")
    if report["stale_count"] > 0:
        click.echo(f"\nStale items ({report['stale_count']}):")
        for s in report["stale_items"][:10]:
            click.echo(f"  [{s['effective_confidence']:.3f}] {s['pattern'][:60]}")
            click.echo(f"    {s['reason']}")
    if archive_result:
        if archive_result.get("archived_count", 0) > 0:
            click.echo(f"\nArchived {archive_result['archived_count']} item(s).")
        elif archive_result.get("would_archive_count", 0) > 0:
            click.echo(f"\nWould archive {archive_result['would_archive_count']} item(s). "
                       f"Use --confirm to execute.")
```

**Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_cli.py::test_maintain_shows_health tests/test_cli.py::test_maintain_json_includes_health tests/test_cli.py::test_maintain_archive_flag_dry_run_by_default tests/test_cli.py::test_maintain_archive_confirm_actually_archives -v`
Expected: All 4 PASS.

**Step 6: Run full test suite**

Run: `uv run python -m pytest -v`
Expected: All tests pass (existing + new).

**Step 7: Commit**

```bash
git add src/vidya/cli.py src/vidya/guidance.py tests/test_cli.py
git commit -m "feat(cli): add vidya maintain — health check, stale detection, optional archive"
```

---

## Workstream C: Structured Action Types

### Task 5: Add action type vocabulary to store.py

**Files:**
- Modify: `src/vidya/store.py`
- Modify: `tests/test_store.py`

**Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
# --- structured action_type validation ---

def test_create_step_accepts_valid_action_types(db):
    """All defined action types are accepted."""
    task_id = create_task(db, goal="test", language="python")
    valid_types = [
        "tool_call", "decision", "discovery", "correction",
        "attempt", "delegation", "configuration",
    ]
    for at in valid_types:
        step_id = create_step(
            db, task_id=task_id, action_type=at,
            action_name="test action", result_status="success",
        )
        assert step_id is not None


def test_create_step_rejects_invalid_action_type(db):
    task_id = create_task(db, goal="test", language="python")
    with pytest.raises(ValueError, match="Invalid action_type"):
        create_step(
            db, task_id=task_id, action_type="random_thing",
            action_name="test", result_status="success",
        )
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_store.py::test_create_step_rejects_invalid_action_type -v`
Expected: FAIL — no validation on `action_type` (currently accepts anything).

**Step 3: Add the vocabulary and validation**

In `src/vidya/store.py`, add after the existing `_VALID_RESULT_STATUSES`:

```python
_VALID_ACTION_TYPES: frozenset[str] = frozenset({
    "tool_call",       # Invoked a tool (file read, write, bash, grep, etc.)
    "decision",        # Chose between alternatives
    "discovery",       # Learned something about the codebase or environment
    "correction",      # Fixed a mistake or changed approach after error
    "attempt",         # Tried something — may succeed or fail
    "delegation",      # Delegated to subagent or external process
    "configuration",   # Changed settings, environment, or config
})
```

Then in `create_step`, add validation at the top of the function body:

```python
    _validate(action_type, _VALID_ACTION_TYPES, "action_type")
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_store.py -v`
Expected: All pass. Check that existing tests that use `action_type="decision"` still pass (they will — "decision" is in the vocabulary).

**Step 5: Run full test suite**

Run: `uv run python -m pytest -v`
Expected: All pass. The CLI `step` command hardcodes `action_type="decision"` which is valid.

**Step 6: Commit**

```bash
git add src/vidya/store.py tests/test_store.py
git commit -m "feat(store): validate action_type against structured vocabulary of 7 types"
```

---

### Task 6: Expose action type in CLI step command

**Files:**
- Modify: `src/vidya/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
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
    # Verify it stored as "decision"
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
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_cli.py::test_step_accepts_action_type -v`
Expected: FAIL — `step` command doesn't accept `--action-type` option.

**Step 3: Update the CLI step command**

In `src/vidya/cli.py`, modify the `step` command to add the `--action-type` option:

```python
@main.command()
@click.option("--task-id", required=True)
@click.option("--action", required=True, help="What was done.")
@click.option("--result", "result_text", required=True, help="What happened.")
@click.option("--outcome", required=True,
              type=click.Choice(["success", "error", "rejected"]))
@click.option("--action-type", default="decision",
              type=click.Choice(["tool_call", "decision", "discovery", "correction",
                                 "attempt", "delegation", "configuration"]),
              help="Category of action taken.")
@click.option("--rationale", default=None)
@click.pass_context
def step(ctx, task_id, action, result_text, outcome, action_type, rationale):
    """Record a step taken during a task."""
    db = _db()
    step_id = create_step(
        db,
        task_id=task_id,
        action_type=action_type,
        action_name=action,
        result_status=outcome,
        result_output=result_text,
        thought=rationale,
    )
```

(The rest of the function body stays the same.)

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_cli.py -v`
Expected: All pass.

**Step 5: Run full test suite**

Run: `uv run python -m pytest -v`
Expected: All pass.

**Step 6: Commit**

```bash
git add src/vidya/cli.py tests/test_cli.py
git commit -m "feat(cli): expose --action-type on step command with 7 structured types"
```

---

### Task 7: Update the using-vidya skill documentation

**Files:**
- Modify: `/Users/quasi/.claude/skills/using-vidya/SKILL.md`

**Step 1: Update the `vidya step` section**

Replace the `vidya step` example in the skill doc to show the new `--action-type` flag:

```markdown
### vidya step

Call for non-obvious decisions. Skip trivial actions.

```bash
vidya step \
  --task-id "$TASK_ID" \
  --action "Chose SQLAlchemy Core over ORM for feature queries" \
  --result "Query works, returns in <50ms" \
  --outcome success \
  --action-type decision \
  --rationale "ORM adds unnecessary abstraction for read-only queries"
```

**Action types** — use the right category for better pattern extraction:

| Type | When to use | Example |
|------|-------------|---------|
| `tool_call` | Invoked a tool or command | "Ran pytest", "Read config.yaml" |
| `decision` | Chose between alternatives (default) | "Chose SQLAlchemy Core over ORM" |
| `discovery` | Learned something about the codebase | "Found that FTS5 needs manual trigger sync" |
| `correction` | Fixed a mistake or changed approach | "Switched from mock to real DB in tests" |
| `attempt` | Tried something — may succeed or fail | "Tried patching the module directly" |
| `delegation` | Delegated to subagent or process | "Dispatched Haiku agents for 170 scenarios" |
| `configuration` | Changed settings or environment | "Enabled WAL mode on SQLite" |
```

Also add to the **Common Mistakes** table:

```markdown
| Using only `decision` for all steps | Pick the right `--action-type` — Vidya uses these to cluster patterns |
```

**Step 2: Commit**

```bash
git add /Users/quasi/.claude/skills/using-vidya/SKILL.md
git commit -m "docs: update using-vidya skill with structured action types"
```

---

### Task 8: Final verification

**Step 1: Run full test suite**

Run: `uv run python -m pytest -v`
Expected: All tests pass.

**Step 2: Manual smoke test of maintain command**

Run against real database:
```bash
vidya maintain
vidya --json maintain
vidya maintain --archive
```

**Step 3: Manual smoke test of structured step**

```bash
vidya task start --goal "test structured steps" --project vidya
# Copy the task_id
vidya step --task-id <id> --action "Tested tool_call type" --result "Accepted" --outcome success --action-type tool_call
vidya step --task-id <id> --action "Tested discovery type" --result "Works" --outcome success --action-type discovery
vidya task end --task-id <id> --outcome success
```

Expected: All commands succeed, steps stored with correct action_type.
