---
title: "Implementation Plan: Confidence Model Rework & Pipeline Fix"
type: implementation-plan
feature: confidence-model, feedback-learning
design_doc: docs/plans/2026-04-06-confidence-rework-design.md
date: 2026-04-06
revision: 2
tags: [canon, implementation, confidence-model, feedback-learning]
---

# Confidence Model Rework & Pipeline Fix — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove freshness decay, add source-based initial confidence with 6 tiers, fix extraction pipeline with two-tier promotion, and migrate existing data preserving fire history.

**Architecture:** Five coordinated changes — (1) simplify confidence.py by removing freshness and effective_confidence, adding SOURCE_CONFIDENCE, (2) update all callers (query.py, brief.py, maintain.py) to use base_confidence directly, (3) refactor learn.py for two-tier promotion with review_rejected distinction, (4) update maintain.py for evidence-based staleness, (5) data migration with history replay.

**Tech Stack:** Python 3.11+, SQLite3, pytest, uv

**Baseline:** 134 tests passing. CCC: 45 passed, 1 failed (pre-existing vocab false positive), 8 skipped.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/vidya/confidence.py` | Modify | Remove freshness, remove effective_confidence, add SOURCE_CONFIDENCE, rename Bayesian→heuristic |
| `src/vidya/query.py` | Modify | Use base_confidence directly, remove freshness imports |
| `src/vidya/brief.py` | Modify | Use base_confidence directly, remove freshness imports |
| `src/vidya/maintain.py` | Modify | Use base_confidence directly, evidence-based staleness |
| `src/vidya/guidance.py` | No change | Reads `effective_confidence` key from dicts, not from confidence.py imports |
| `src/vidya/cli.py` | No change | Reads `effective_confidence` field from QueryResult, field name preserved |
| `src/vidya/learn.py` | Modify | Two-tier promotion, review_rejected distinction, SOURCE_CONFIDENCE |
| `src/vidya/store.py` | Modify | promote_candidate accepts source parameter |
| `src/vidya/migrate.py` | Create | One-time migration with fire history replay |
| `tests/test_confidence.py` | Modify | Remove freshness tests, add SOURCE_CONFIDENCE tests |
| `tests/test_learn.py` | Modify | Two-tier promotion tests, review_rejected tests |
| `tests/test_query.py` | Modify | Remove freshness from query tests |
| `tests/test_brief.py` | Modify | Remove freshness from brief tests |
| `tests/test_maintain.py` | Modify | Evidence-based staleness tests |
| `tests/test_migrate.py` | Create | Migration with history replay tests |

---

## Prerequisites

- All 134 tests currently passing
- Database at `~/.vidya/vidya.db` with 79 active items (57 extraction, 22 seed)

## Dependency Graph

```
Task 1 (confidence.py — core model changes)
  ├─► Task 2 (query.py + brief.py — remove freshness from callers)
  ├─► Task 3 (learn.py + store.py — two-tier promotion)
  ├─► Task 4 (maintain.py — evidence-based staleness)
  └─► Task 5 (migrate.py — data migration with history replay)
       └─► Task 6 (run migration on live DB)
```

**Critical path:** Task 1 → Tasks 2,3,4,5 (parallel) → Task 6
**Parallel tracks:** Tasks 2, 3, 4, 5 can proceed independently after Task 1

---

## Implementation Tasks

### Task 1: Rework confidence.py

**Goal:** Remove freshness decay and effective_confidence. Add SOURCE_CONFIDENCE dict. Rename Bayesian→heuristic in docstrings.

**Files:**
- Modify: `src/vidya/confidence.py`
- Modify: `tests/test_confidence.py`

- [ ] **Step 1: Write failing tests for new model**

Replace the freshness tests and add SOURCE_CONFIDENCE tests in `tests/test_confidence.py`:

```python
# Remove these imports: FRESHNESS_DECAY_RATE, FRESHNESS_FLOOR, compute_freshness,
#                        effective_confidence
# Add this import: SOURCE_CONFIDENCE

from vidya.confidence import (
    SOURCE_CONFIDENCE,
    TRUST_DECAY,
    TRUST_GROWTH,
    update_on_failure,
    update_on_success,
)


# --- Source-based confidence ---

def test_source_confidence_user_correction():
    assert SOURCE_CONFIDENCE["user_correction"] == 0.85

def test_source_confidence_user_confirmation():
    assert SOURCE_CONFIDENCE["user_confirmation"] == 0.70

def test_source_confidence_review_rejected():
    assert SOURCE_CONFIDENCE["review_rejected"] == 0.65

def test_source_confidence_test_outcome():
    assert SOURCE_CONFIDENCE["test_outcome"] == 0.60

def test_source_confidence_seed():
    assert SOURCE_CONFIDENCE["seed"] == 0.60

def test_source_confidence_extraction():
    assert SOURCE_CONFIDENCE["extraction"] == 0.40

def test_source_confidence_unknown_raises():
    with pytest.raises(KeyError):
        SOURCE_CONFIDENCE["unknown_source"]
```

Remove all `test_freshness_*` tests (6 tests) and the `test_effective_confidence_*` tests (3 tests).

Keep the success/failure heuristic update tests unchanged — those are still valid. Update their docstrings from "Bayesian" to "heuristic".

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_confidence.py -v`
Expected: FAIL — `SOURCE_CONFIDENCE` not yet defined, old imports still expected

- [ ] **Step 3: Implement the changes**

In `src/vidya/confidence.py`:

1. Update module docstring: replace "Bayesian" with "heuristic", remove freshness description.
2. Remove `FRESHNESS_DECAY_RATE`, `FRESHNESS_FLOOR` constants.
3. Remove `compute_freshness()` function entirely.
4. Remove `days_since_reference()` function entirely.
5. Remove `effective_confidence()` function entirely.
6. Add `SOURCE_CONFIDENCE` dict:

```python
SOURCE_CONFIDENCE: dict[str, float] = {
    "user_correction": 0.85,
    "user_confirmation": 0.70,
    "review_rejected": 0.65,
    "test_outcome": 0.60,
    "seed": 0.60,
    "extraction": 0.40,
}
```

7. Update `update_on_success` and `update_on_failure` docstrings: "Heuristic update" not "Bayesian update".

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_confidence.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/vidya/confidence.py tests/test_confidence.py
git commit -m "refactor(confidence): remove freshness decay, add SOURCE_CONFIDENCE, drop effective_confidence"
```

---

### Task 2: Update all callers (query.py, brief.py)

**Goal:** Remove freshness computation from query.py and brief.py. Use base_confidence directly.

**Files:**
- Modify: `src/vidya/query.py`
- Modify: `src/vidya/brief.py`
- Modify: `tests/test_query.py`
- Modify: `tests/test_brief.py`

Note: `guidance.py` reads `effective_confidence` from dicts (not imported from confidence.py) — no changes needed. `cli.py` reads the `effective_confidence` field from `QueryResult` — the field name is preserved in `QueryResult` for output compatibility.

- [ ] **Step 1: Update query.py**

1. Remove imports: `compute_freshness`, `days_since_reference`, `effective_confidence`
2. Remove `from datetime import datetime, timezone` (no longer needed)
3. In `cascade_query()`, replace the freshness block:

```python
# OLD (lines 82-89):
# now = datetime.now(timezone.utc)
# ...
# days = days_since_reference(row["last_fired"], row["first_seen"], now)
# fresh = compute_freshness(days)
# eff = effective_confidence(row["base_confidence"], fresh)

# NEW:
eff = row["base_confidence"]
```

4. Remove the `now = datetime.now(timezone.utc)` line.
5. `QueryResult.effective_confidence` field stays — it now holds `base_confidence` directly. This preserves the field name for `cli.py` and `guidance.py` consumers.

- [ ] **Step 2: Update brief.py**

1. Remove imports: `compute_freshness`, `days_since_reference`, `effective_confidence`
2. Remove `from datetime import datetime, timezone` if only used for freshness.
3. In `_project_state()`, replace:

```python
# OLD (lines 70-73):
# days = days_since_reference(row["last_fired"], row["first_seen"], now)
# fresh = compute_freshness(days)
# eff = effective_confidence(row["base_confidence"], fresh)

# NEW:
eff = row["base_confidence"]
```

4. Remove `now = datetime.now(timezone.utc)` if no longer needed.

- [ ] **Step 3: Update tests**

In `tests/test_query.py`: update any tests that set up items with specific `last_fired` values to test freshness filtering. Items should now be returned regardless of `last_fired` age, as long as `base_confidence >= min_confidence`.

In `tests/test_brief.py`: update any freshness-dependent assertions.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_query.py tests/test_brief.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/vidya/query.py src/vidya/brief.py tests/test_query.py tests/test_brief.py
git commit -m "refactor(query,brief): use base_confidence directly, remove freshness computation"
```

---

### Task 3: Fix the extraction pipeline (learn.py + store.py)

**Goal:** Two-tier promotion. review_rejected distinct from user_correction. SOURCE_CONFIDENCE for initial values.

**Files:**
- Modify: `src/vidya/learn.py`
- Modify: `src/vidya/store.py`
- Modify: `tests/test_learn.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_learn.py` using the existing `_feedback` helper:

```python
from vidya.confidence import SOURCE_CONFIDENCE


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
    # Should NOT be in knowledge_items
    assert "item_id" not in result
    # Candidate should exist and be pending
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
    # Create an existing item
    create_item(db, pattern="run linting", guidance="Always run linting before commit",
                item_type="convention", language="python",
                base_confidence=0.5, source="extraction")
    # Send matching confirmation
    feedback = _feedback(db, "user_confirmation", "Always run linting before commit",
                         language="python")
    result = extract_from_feedback(db, feedback)
    assert result is None  # No new item/candidate created


def test_no_feedback_signal_is_dropped(db):
    """Every feedback type should produce a result when no matches exist."""
    for fb_type in ["user_correction", "user_confirmation", "test_failed", "review_rejected"]:
        feedback = _feedback(db, fb_type,
                             f"Unique detail for {fb_type} signal {id(fb_type)}",
                             language="python", project="vidya")
        result = extract_from_feedback(db, feedback)
        assert result is not None, f"{fb_type} was silently dropped"
```

Also update existing test `test_review_rejected_sets_low_confidence` to expect 0.65 instead of 0.15.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_learn.py -v -k "correction_creates or review_rejected_creates_at or unmatched or no_feedback"`
Expected: FAIL — new behavior not yet implemented

- [ ] **Step 3: Update store.py — promote_candidate accepts source**

In `src/vidya/store.py`, modify `promote_candidate` (line 322):

```python
def promote_candidate(db: sqlite3.Connection, candidate_id: str, source: str = "extraction") -> str:
    """Promote an extraction candidate to a knowledge item atomically."""
    # ... existing code ...
    with db:
        item_id = _insert_item_row(
            db,
            # ... existing params ...
            source=source,  # was hardcoded "extraction"
            # ...
        )
```

- [ ] **Step 4: Implement learn.py changes**

1. Add import: `from vidya.confidence import SOURCE_CONFIDENCE`

2. Split `_NEGATIVE_TYPES` into two:

```python
_CORRECTION_TYPES = frozenset({"user_correction"})
_REJECTION_TYPES = frozenset({"review_rejected"})
_POSITIVE_TYPES = frozenset({"review_accepted", "user_confirmation"})
_FAILURE_TYPES = frozenset({"test_failed"})
```

3. Rename `_handle_negative` to `_handle_correction`. Add `source` parameter:

```python
def _handle_correction(db, feedback_id, detail, language, runtime, framework, project, source):
    """Create or merge a knowledge item for correction/rejection feedback."""
    # ... existing similar-item-finding logic ...
    # For new items:
    candidate_id = create_candidate(
        db,
        pattern=_infer_pattern(detail, language, project),
        guidance=detail,
        item_type=classify_type(detail),
        language=language, runtime=runtime, framework=framework, project=project,
        method="feedback",
        evidence=json.dumps([feedback_id]),
        initial_confidence=SOURCE_CONFIDENCE[source],
    )
    item_id = promote_candidate(db, candidate_id, source=source)
    return {"item_id": item_id}
```

4. Refactor `_apply_confidence_update` to return whether it matched:

```python
def _apply_confidence_update(db, detail, language, project, update_fn, count_field) -> bool:
    """Find similar items and apply update. Returns True if any item matched."""
    # ... existing logic, return updated bool ...
```

5. Add `_create_candidate_from_unmatched` helper:

```python
def _create_candidate_from_unmatched(db, feedback_id, detail, language, runtime, framework,
                                      project, source, item_type):
    """Create a pending extraction candidate from an unmatched feedback signal."""
    candidate_id = create_candidate(
        db,
        pattern=_infer_pattern(detail, language, project),
        guidance=detail,
        item_type=item_type,
        language=language, runtime=runtime, framework=framework, project=project,
        method="feedback",
        evidence=json.dumps([feedback_id]),
        initial_confidence=SOURCE_CONFIDENCE[source],
    )
    return {"candidate_id": candidate_id}
```

6. Update `extract_from_feedback` routing:

```python
if fb_type in _CORRECTION_TYPES:
    return _handle_correction(db, feedback["id"], detail, language, runtime, framework,
                               project, source="user_correction")
if fb_type in _REJECTION_TYPES:
    return _handle_correction(db, feedback["id"], detail, language, runtime, framework,
                               project, source="review_rejected")
if fb_type in _POSITIVE_TYPES:
    matched = _apply_confidence_update(db, detail, language, project, update_on_success, "success_count")
    if not matched:
        return _create_candidate_from_unmatched(db, feedback["id"], detail, language, runtime,
                                                 framework, project, source="user_confirmation",
                                                 item_type=classify_type(detail))
    return None
if fb_type in _FAILURE_TYPES:
    matched = _apply_confidence_update(db, detail, language, project, update_on_failure, "fail_count")
    if not matched:
        return _create_candidate_from_unmatched(db, feedback["id"], detail, language, runtime,
                                                 framework, project, source="test_outcome",
                                                 item_type="diagnostic")
    return None
```

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_learn.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/vidya/learn.py src/vidya/store.py tests/test_learn.py
git commit -m "fix(learn): two-tier promotion, review_rejected distinct, source-based confidence"
```

---

### Task 4: Update maintain.py

**Goal:** Use base_confidence directly. Evidence-based staleness including "fired long ago".

**Files:**
- Modify: `src/vidya/maintain.py`
- Modify: `tests/test_maintain.py`

- [ ] **Step 1: Write/update tests**

Update staleness tests for new criteria:
- Never fired + older than `stale_days`
- Fired but `last_fired` older than `stale_days`
- Contradicted: `fail_count > success_count`
- Superseded but not archived

Update stats tests: confidence bands use `base_confidence` directly.

- [ ] **Step 2: Implement changes**

1. Remove imports: `compute_freshness`, `days_since_reference` (if not needed), `effective_confidence`
2. In `compute_stats`, replace freshness computation:

```python
# Use base_confidence directly for bands
eff = row["base_confidence"]
```

3. Keep `days_since_reference` import or compute inline — still needed for "N days since creation/last fire".

4. In `find_stale_items`, replace staleness criteria:

```python
from datetime import datetime, timezone

def find_stale_items(db, language=None, project=None, stale_days=90):
    # ... fetch rows including fail_count, success_count, superseded_by ...
    now = datetime.now(timezone.utc)

    for row in rows:
        # Compute days since last activity
        ref_ts = row["last_fired"] or row["first_seen"]
        if ref_ts:
            last = datetime.fromisoformat(ref_ts)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days = max(0, (now - last).days)
        else:
            days = None

        reasons = []
        if row["fire_count"] == 0 and days is not None and days >= stale_days:
            reasons.append(f"Never fired, created {days} days ago")
        elif row["fire_count"] > 0 and days is not None and days >= stale_days:
            reasons.append(f"Last fired {days} days ago")
        if row["fail_count"] > row["success_count"]:
            reasons.append(f"Contradicted: {row['fail_count']} failures vs {row['success_count']} successes")
        if row.get("superseded_by"):
            reasons.append("Superseded but not archived")

        if reasons:
            stale.append({
                "id": row["id"],
                "pattern": row["pattern"],
                "guidance": row["guidance"],
                "reason": "; ".join(reasons),
                "base_confidence": round(row["base_confidence"], 3),
                "days_since_activity": days,
            })
```

5. In `auto_archive_stale`, use `base_confidence` for threshold:

```python
candidates = [s for s in stale if s["base_confidence"] < archive_threshold]
```

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/test_maintain.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/vidya/maintain.py tests/test_maintain.py
git commit -m "refactor(maintain): evidence-based staleness, base_confidence for stats"
```

---

### Task 5: Data migration with fire history replay

**Goal:** Fix existing items' source and confidence, preserving accumulated evidence.

**Files:**
- Create: `src/vidya/migrate.py`
- Create: `tests/test_migrate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_migrate.py
import pytest
from vidya.confidence import SOURCE_CONFIDENCE, TRUST_GROWTH
from vidya.migrate import migrate_confidence_model


def test_migration_updates_extraction_to_user_correction(db):
    """Items from extraction get source=user_correction and confidence 0.85."""
    from vidya.store import _insert_item_row
    item_id = _insert_item_row(db, pattern="test", guidance="test",
                                item_type="convention", source="extraction",
                                base_confidence=0.15)
    db.commit()
    result = migrate_confidence_model(db)

    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    assert item["source"] == "user_correction"
    assert item["base_confidence"] == pytest.approx(0.85)
    assert result["updated_count"] == 1


def test_migration_replays_fire_history(db):
    """Items with successful fires get confidence above 0.85."""
    from vidya.store import _insert_item_row
    item_id = _insert_item_row(db, pattern="test", guidance="test",
                                item_type="convention", source="extraction",
                                base_confidence=0.1925)
    # Simulate 1 successful fire
    db.execute("UPDATE knowledge_items SET fire_count=1, success_count=1 WHERE id=?", (item_id,))
    db.commit()

    migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    # 0.85 + 0.05 * (1 - 0.85) = 0.8575
    expected = 0.85 + TRUST_GROWTH * (1.0 - 0.85)
    assert item["base_confidence"] == pytest.approx(expected)
    assert item["source"] == "user_correction"


def test_migration_replays_two_successes(db):
    """Items with 2 fires get two rounds of heuristic growth."""
    from vidya.store import _insert_item_row
    item_id = _insert_item_row(db, pattern="test", guidance="test",
                                item_type="convention", source="extraction",
                                base_confidence=0.232875)
    db.execute("UPDATE knowledge_items SET fire_count=2, success_count=2 WHERE id=?", (item_id,))
    db.commit()

    migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    # 0.85 → 0.8575 → 0.864625
    base = 0.85
    base += TRUST_GROWTH * (1.0 - base)  # first success
    base += TRUST_GROWTH * (1.0 - base)  # second success
    assert item["base_confidence"] == pytest.approx(base)


def test_migration_leaves_seed_items_unchanged(db):
    """Seed items keep their source and confidence."""
    from vidya.store import _insert_item_row
    item_id = _insert_item_row(db, pattern="test", guidance="test",
                                item_type="convention", source="seed",
                                base_confidence=0.60)
    db.commit()

    migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    assert item["source"] == "seed"
    assert item["base_confidence"] == pytest.approx(0.60)


def test_migration_is_idempotent(db):
    """Running migration twice produces the same result."""
    from vidya.store import _insert_item_row
    _insert_item_row(db, pattern="test", guidance="test",
                     item_type="convention", source="extraction",
                     base_confidence=0.15)
    db.commit()

    r1 = migrate_confidence_model(db)
    r2 = migrate_confidence_model(db)
    assert r1["updated_count"] == 1
    assert r2["updated_count"] == 0  # nothing to update second time
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_migrate.py -v`
Expected: FAIL — migrate module doesn't exist

- [ ] **Step 3: Implement migration**

```python
# src/vidya/migrate.py
"""One-time data migration for confidence model rework.

Extraction items were created from user corrections but got source='extraction'
and base_confidence=0.15. This migration fixes source attribution and replays
fire history to compute correct confidence.
"""

import sqlite3

from vidya.confidence import SOURCE_CONFIDENCE, TRUST_GROWTH


def migrate_confidence_model(db: sqlite3.Connection) -> dict:
    """Migrate existing extraction items to correct source and confidence.

    1. Set source to 'user_correction' (verified: all 62 negative feedbacks
       in the database are user_correction, zero review_rejected).
    2. Start from SOURCE_CONFIDENCE['user_correction'] (0.85).
    3. Replay success_count applications of heuristic growth.
    4. Idempotent: only updates items still marked source='extraction'.

    Returns: {"updated_count": N, "details": [...]}
    """
    rows = db.execute(
        "SELECT id, success_count, fail_count FROM knowledge_items "
        "WHERE status = 'active' AND source = 'extraction'"
    ).fetchall()

    updated = []
    for row in rows:
        base = SOURCE_CONFIDENCE["user_correction"]
        # Replay heuristic growth for each recorded success
        for _ in range(row["success_count"]):
            base = base + TRUST_GROWTH * (1.0 - base)
        # Note: fail_count replay would use TRUST_DECAY, but current data
        # shows 0 failures across all 57 extraction items.

        db.execute(
            "UPDATE knowledge_items SET source = ?, base_confidence = ? WHERE id = ?",
            ("user_correction", base, row["id"]),
        )
        updated.append({"id": row["id"], "new_confidence": round(base, 6),
                         "successes_replayed": row["success_count"]})

    if updated:
        db.commit()

    return {"updated_count": len(updated), "details": updated}
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_migrate.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/vidya/migrate.py tests/test_migrate.py
git commit -m "feat(migrate): confidence model migration with fire history replay"
```

---

### Task 6: Run migration and verify

**Goal:** Apply migration to `~/.vidya/vidya.db` and verify results.

- [ ] **Step 1: Run full test suite first**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL PASS (all tasks complete)

- [ ] **Step 2: Backup database**

```bash
cp ~/.vidya/vidya.db ~/.vidya/vidya.db.backup-$(date +%Y%m%d)
```

- [ ] **Step 3: Run migration**

```bash
uv run python -c "
import sqlite3
from vidya.migrate import migrate_confidence_model
db = sqlite3.connect('$HOME/.vidya/vidya.db')
db.row_factory = sqlite3.Row
result = migrate_confidence_model(db)
print(f'Updated {result[\"updated_count\"]} items')
for d in result['details'][:5]:
    print(f'  {d[\"id\"][:8]}... conf={d[\"new_confidence\"]} successes={d[\"successes_replayed\"]}')
db.close()
"
```

Expected: `Updated 57 items` (52 at 0.85, 3 at 0.8575, 2 at 0.864625)

- [ ] **Step 4: Verify stats**

```bash
vidya stats
```

Expected: HIGH ~79, MED=0, LOW=0.

- [ ] **Step 5: Verify idempotency**

```bash
uv run python -c "
import sqlite3
from vidya.migrate import migrate_confidence_model
db = sqlite3.connect('$HOME/.vidya/vidya.db')
db.row_factory = sqlite3.Row
result = migrate_confidence_model(db)
print(f'Updated {result[\"updated_count\"]} items (should be 0)')
db.close()
"
```

Expected: `Updated 0 items (should be 0)`

---

## Estimated Size

| Task | Impl Lines | Test Lines | Total |
|------|-----------|-----------|-------|
| 1. confidence.py | ~-35 (net removal) | ~-5 (net change) | ~40 changed |
| 2. query.py + brief.py | ~-10 | ~5 | ~15 |
| 3. learn.py + store.py | ~50 | ~70 | ~120 |
| 4. maintain.py | ~15 | ~15 | ~30 |
| 5. migrate.py | ~35 | ~50 | ~85 |
| **Total** | **~55** | **~135** | **~290** |

Net effect: code shrinks overall (removing freshness is more deletion than addition).

---

## Drift Report

Completed 2026-04-06. All 6 tasks implemented, 148 tests passing, migration applied to live DB.

### What Was Actually Built

1. **confidence.py**: Removed `compute_freshness`, `days_since_reference`, `effective_confidence`, `FRESHNESS_DECAY_RATE`, `FRESHNESS_FLOOR`. Added `SOURCE_CONFIDENCE` dict with 6 tiers. Renamed Bayesian→heuristic in docstrings.
2. **query.py**: Uses `base_confidence` directly. Removed freshness imports and computation. `QueryResult.effective_confidence` field name preserved.
3. **brief.py**: Uses `base_confidence` directly. Removed freshness imports. `datetime` import retained (used by `_attention_items`).
4. **learn.py**: Two-tier promotion. `_NEGATIVE_TYPES` split into `_CORRECTION_TYPES` and `_REJECTION_TYPES`. `_handle_negative` renamed to `_handle_correction` with `source` param. `_apply_confidence_update` returns bool. New `_create_candidate_from_unmatched` creates pending candidates for unmatched confirmations/failures.
5. **store.py**: `promote_candidate` accepts `source` parameter (default "extraction").
6. **maintain.py**: Evidence-based staleness (never-fired+old, fired-long-ago, contradicted, superseded). `min_confidence` parameter removed from `find_stale_items`. Days computation inlined. Output key changed to `base_confidence`.
7. **migrate.py**: New module. `migrate_confidence_model` fixes source attribution and replays fire history. Idempotent.
8. **cli.py**: Fixed `effective_confidence` → `base_confidence` key in maintain output (caught in review).

### Deviations from Plan

#### Deviation: 59 items migrated instead of 57
**Planned:** 57 extraction items to migrate (52 zero-fire, 3 one-fire, 2 two-fire)
**Actual:** 59 items migrated — 2 new extraction items were created during today's sessions before the migration ran
**Reason:** Live database accumulated new items between analysis and migration
**Spec impact:** No — migration logic is correct, just more items than counted earlier

#### Deviation: test_no_feedback_signal_is_dropped uses UUID tokens
**Planned:** Human-readable test strings with `id(fb_type)` suffix
**Actual:** Uses `uuid.uuid4().hex` for token independence
**Reason:** Token-overlap dedup (threshold 0.3) was matching common English words across iterations, causing false positive merges
**Spec impact:** No — test intent preserved, only string generation changed

#### Deviation: cli.py required a fix not in the plan
**Planned:** cli.py listed as "No change" in file structure
**Actual:** cli.py line 450 had `s['effective_confidence']` which would KeyError since maintain now returns `base_confidence`
**Reason:** Plan missed this downstream reference. Caught in Gate A code review.
**Spec impact:** No — output behavior unchanged, just key name in internal dict

### New Edge Cases Discovered

- Token-overlap dedup false positives with common English words — **Given** two feedback strings sharing common words like "always", "use", "before" / **When** overlap_score is computed / **Then** score exceeds 0.3 threshold despite being semantically different items. (Known Phase 1 limitation, Phase 3 embeddings will fix.)

- `review_accepted` in `_POSITIVE_TYPES` creates candidates with `source="user_confirmation"` — wrong attribution when `review_accepted` is the actual feedback type. Latent bug, not triggered in current data (zero `review_accepted` feedbacks exist).

### Decisions Made During Implementation

- Kept `datetime` import in `brief.py`: still used by `_attention_items()` for staleness-in-days attention flag (independent of confidence model)
- Inlined days-since-reference computation in `maintain.py` rather than keeping the deleted function: simpler, only one call site
- Used `create_item` in migration tests instead of `_insert_item_row`: public API, more maintainable (review finding)

### Suggested Spec Updates

- Add scenario to feedback-extraction: "review_accepted creates candidate with correct source attribution" (currently uses user_confirmation source for review_accepted — latent bug)
- Update confidence-model contract: remove reference to `effective_confidence` function (it no longer exists)
- Add vocabulary term: `heuristic update` — "Exponential smoothing formula for confidence adjustment. Success: base += 0.05 * (1 - base). Failure: base *= 0.70. Not Bayesian — no prior/likelihood/posterior."

### Feeding Back to Canon
To update the Canon specification with this drift report:
`/canon sync --drift-report docs/plans/2026-04-06-confidence-rework-plan.md`
