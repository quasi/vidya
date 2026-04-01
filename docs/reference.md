# Vidya Reference

Vidya accumulates procedural knowledge from agent sessions and serves it back. Knowledge lives in a SQLite database, not in any agent's context. It survives across sessions, machines, and tool changes.

**Database location**: `~/.vidya/vidya.db`

**Interfaces**: CLI (`vidya`) and MCP server (`vidya-server`). Both wrap the same Python library — no logic lives in the interface layer.

---

## Contents

1. [Knowledge Model](#knowledge-model)
2. [Confidence Model](#confidence-model)
3. [Scope Hierarchy](#scope-hierarchy)
4. [CLI Reference](#cli-reference)
5. [MCP Tool Reference](#mcp-tool-reference)
6. [Seed File Format](#seed-file-format)
7. [Learning: How Feedback Works](#learning-how-feedback-works)
8. [Database Tables](#database-tables)

---

## Knowledge Model

Each knowledge item answers two questions: **when does this apply** (pattern) and **what to do** (guidance).

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | TEXT | UUID, primary key |
| `pattern` | TEXT | When this item applies (semantic condition) |
| `guidance` | TEXT | What to do |
| `type` | TEXT | `convention`, `anti_pattern`, `precondition`, `postcondition` (see Item types below) |
| `language` | TEXT | Scope: `python`, `common-lisp`, etc. NULL = global |
| `runtime` | TEXT | Scope: `cpython-3.12`, `sbcl`, etc. NULL = any |
| `framework` | TEXT | Scope: `fastapi`, `asdf`, etc. NULL = any |
| `project` | TEXT | Scope: `canon`, `pooler`, etc. NULL = any project |
| `base_confidence` | REAL | Epistemic trust (0–1). Updated by Bayesian formula on observed outcomes. |
| `source` | TEXT | `seed`, `observation`, `extraction` |
| `tags` | TEXT | JSON array of strings. Stored but not yet queried (Phase 2). |
| `details_json` | TEXT | Structured payload, NULL in Phase 1 |
| `overrides` | TEXT | Item ID this item overrides (suppresses in query results) |
| `status` | TEXT | `active`, `archived`, `evicted` |
| `first_seen` | TEXT | ISO 8601 timestamp |
| `last_fired` | TEXT | ISO 8601 timestamp of last successful query match. NULL if never fired. |
| `fire_count` | INTEGER | Times returned in query results |
| `success_count` | INTEGER | Times positively confirmed |
| `fail_count` | INTEGER | Times negatively confirmed |
| `evidence` | TEXT | JSON array of feedback/observation IDs |
| `explanation` | TEXT | Free-text explanation of why the item exists |

### Item types

| Type | Meaning | Typical trigger words |
|------|---------|----------------------|
| `convention` | What to do | "always", "must", "should" (also the default fallback) |
| `anti_pattern` | What not to do | "don't", "never", "avoid" |
| `precondition` | Check before acting | "before", "first", "ensure" |
| `postcondition` | Verify after acting | "after", "then", "verify" |

Classification happens automatically during `vidya feedback` and `vidya seed` based on keyword detection in the guidance text. Text that matches none of the above keywords is classified as `convention`. Override with explicit `--type` is not yet exposed; edit the DB directly if needed.

---

## Confidence Model

Two separate numbers combine to produce a ranking score.

### base_confidence

Epistemic trust. Starts at the seeded value (typically 0.5–0.6) or 0.15 for auto-extracted items. Updated by Bayesian formulas:

```
On success:  base_confidence += 0.05 × (1.0 − base_confidence)
On failure:  base_confidence ×= 0.70
```

Recovery example: from 0.64, one failure drops to 0.448. Recovering to 0.64 requires ~8 successes. The system is slow to trust, quick to doubt.

### freshness

Temporal staleness. Computed at query time from `last_fired` — never stored.

```
freshness = max(0.3, 1.0 − 0.005 × days_since_last_fired)
```

- Freshness floor: **0.3** (items are never fully forgotten)
- Full decay to floor: **140 days** without firing
- Recovery: firing the item sets `last_fired = now`, so freshness returns to 1.0 immediately

For items that have **never been fired** (`last_fired` is NULL): the query engine substitutes `first_seen` as the reference timestamp before calling `compute_freshness`. An item created today has `first_seen` = today, so `days_since = 0` and `freshness = 1.0`. New items start fresh, not stale.

### effective_confidence

```
effective_confidence = base_confidence × freshness
```

Computed at query time, never stored.

### Confidence bands

| Band | Threshold | Behavior |
|------|-----------|----------|
| HIGH | > 0.5 | Returned in queries; agent should follow |
| MEDIUM | 0.2 – 0.5 | Returned in queries; treat as provisional |
| LOW | < 0.2 | Excluded from normal queries; visible in `explain` and `stats` |

The default `--min-confidence` is 0.2.

---

## Scope Hierarchy

Items are scoped from broadest to narrowest. A query at project scope sees items from all applicable levels.

```
global           (language=NULL, runtime=NULL, framework=NULL, project=NULL)
  language       (language set, rest NULL)
    runtime      (language + runtime set, rest NULL)
  framework      (framework set, language=NULL — tool knowledge, matches any language)
    framework    (language + framework set — language-specific tool knowledge)
      project    (language + project set)
```

**Framework as tool knowledge**: Items with `framework` set but `language=NULL` represent language-independent tool knowledge (e.g. how to use Canon, Docker, or pytest effectively). These match in any language context when the framework is specified in the query. Items with both `language` and `framework` set are language-specific tool knowledge.

**Query cascade**: All matching scope levels are fetched. Narrower scope gets a higher boost multiplier when ranking results.

| Scope level | Boost |
|-------------|-------|
| project | 1.5× |
| framework | 1.3× |
| runtime | 1.1× |
| language | 1.0× |
| global | 0.9× |

**Override suppression**: If item A has `overrides = <item B's ID>`, item B is excluded from results whenever item A appears. Used when a narrower item replaces a broader one on the same topic.

---

## CLI Reference

All commands read from and write to `~/.vidya/vidya.db`.

### `vidya query`

Return ranked knowledge items for the current context.

```
vidya query --language LANG --context TEXT [options]
```

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--language` | yes | — | Language scope |
| `--context` | yes | — | What you're doing (semantic description) |
| `--runtime` | no | — | Runtime scope |
| `--framework` | no | — | Framework scope |
| `--project` | no | — | Project scope |
| `--goal` | no | — | Additional context for FTS ranking |
| `--min-confidence` | no | 0.2 | Exclude items below this effective confidence |

**Example:**

```bash
vidya query --language python --project canon --context "run pytest test"
```

```
[HIGH 0.60] [precondition] [project]
  Pattern:  Always run full test suite before
  Guidance: Always run the full test suite before recording a task complete: `cd src/cli && uv run pytest tests/ -v`
  Reason:   scope=project, language=python, project=canon, fts_match=1.00

[HIGH 0.60] [convention] [project]
  Pattern:  Always use `uv run pytest` not
  Guidance: Always use `uv run pytest` not bare `pytest`
  Reason:   scope=project, language=python, project=canon, fts_match=0.73
```

**How ranking works**: FTS5 tokenizes the context into individual words joined with OR. If any items match the FTS query, non-matching items are excluded entirely. The final score is `(1 + fts_score) × effective_confidence × scope_boost`.

---

### `vidya feedback`

Record feedback and trigger knowledge extraction.

```
vidya feedback --type TYPE --detail TEXT [options]
```

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--type` | yes | — | See feedback types below |
| `--detail` | yes | — | The correction, confirmation, or result |
| `--language` | no | — | Scope for the feedback |
| `--runtime` | no | — | Scope |
| `--framework` | no | — | Scope |
| `--project` | no | — | Scope |
| `--task-id` | no | — | Link to an active task |

**Feedback types and what they trigger:**

| Type | Triggers |
|------|----------|
| `user_correction` | Creates or merges a knowledge item (negative signal) |
| `review_rejected` | Creates or merges a knowledge item (negative signal) |
| `user_confirmation` | Boosts confidence on matching items (positive signal) |
| `review_accepted` | Boosts confidence on matching items (positive signal) |
| `test_failed` | Decays confidence on matching items |
| `test_passed` | No effect on knowledge items (recorded only) |

**Output paths** (one of three):

```
Created new knowledge item: <uuid>        # new item, no existing overlap
Merged into existing item: <uuid>         # feedback merged into existing item
Updated existing items.                   # positive/failure feedback updated confidence
```

**Example — correction that creates a new item:**

```bash
vidya feedback \
  --type user_correction \
  --detail "Always use uv run python -m pytest, never bare pytest" \
  --language python \
  --project canon
```

```
Created new knowledge item: a3f1c2...
```

**Example — confirmation that boosts existing items:**

```bash
vidya feedback \
  --type user_confirmation \
  --detail "uv run pytest is correct" \
  --language python \
  --project canon
```

```
Updated existing items.
```

**Deduplication**: before creating a new item, Vidya checks existing items for overlap using FTS5. If the fraction of new-feedback tokens found in an existing item exceeds 0.5, the feedback merges into that item (boosts confidence, appends evidence) rather than creating a duplicate.

---

### `vidya seed`

Extract actionable rules from a markdown file and insert as knowledge items.

```
vidya seed --file PATH [options]
```

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--file` | yes | — | Path to markdown rules file |
| `--language` | no | — | Apply this language scope to all items |
| `--runtime` | no | — | Apply this runtime scope |
| `--framework` | no | — | Apply this framework scope |
| `--project` | no | — | Apply this project scope |
| `--confidence` | no | 0.5 | Base confidence for seeded items |

**Example:**

```bash
vidya seed \
  --file scripts/seeds/canon.md \
  --language python \
  --project canon \
  --confidence 0.6
```

```
Created 22 knowledge item(s).
```

All items share the same scope and confidence. The entire file is inserted in one transaction. Duplicates (overlap score ≥ 0.5 with an existing item) are silently skipped. See [Seed File Format](#seed-file-format) for how rules are extracted from markdown.

---

### `vidya stats`

Show knowledge base statistics.

```
vidya stats [--language LANG] [--project PROJECT]
```

Without filters, reports across the entire database.

**Example:**

```bash
vidya stats --language python --project canon
```

```
Total items:      22
By confidence:    HIGH=22  MED=0  LOW=0
By scope:         {'global': 0, 'language': 0, 'runtime': 0, 'framework': 0, 'project': 22}
By type:          {'convention': 8, 'anti_pattern': 10, 'postcondition': 3, 'precondition': 1}
Total tasks:      0
Total feedback:   0
Total candidates: 0
```

Confidence bands are computed from `effective_confidence` (base × freshness at query time), not from `base_confidence` alone.

---

### `vidya items`

List active knowledge items with optional filters.

```
vidya items [--language LANG] [--project PROJECT] [--min-confidence FLOAT]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--language` | — | Filter by language |
| `--project` | — | Filter by project |
| `--min-confidence` | 0.0 | Exclude items below this base_confidence |

**Example:**

```bash
vidya items --language python --project canon --min-confidence 0.5
```

```
[0.60] [postcondition] [canon] Always update `docs/CHANGELOG.md` after every substantive
  Always update `docs/CHANGELOG.md` after every substantive change
[0.60] [anti_pattern] [canon] Canon DB lives `.canon/canon.db` — never
  The Canon DB lives at `.canon/canon.db` — never create it elsewhere
...
```

Format: `[base_confidence] [type] [scope] pattern (truncated to 6 words)` then guidance (truncated to 80 chars).

Note: `--min-confidence` here filters on `base_confidence`, not `effective_confidence`. Use `vidya query` with `--min-confidence` for effective-confidence filtering.

---

### `vidya explain`

Show the full record for a knowledge item.

```
vidya explain --item-id UUID
```

Prints the complete database row as JSON. Does not show overriding items — use the MCP tool `vidya_explain` for that.

**Example:**

```bash
vidya explain --item-id 07a48f57-d87f-4f7c-9955-57e8c2a4fa4f
```

```json
{
  "id": "07a48f57-d87f-4f7c-9955-57e8c2a4fa4f",
  "language": "python",
  "project": "canon",
  "pattern": "Never bypass control loop quick fixes",
  "guidance": "Never bypass the control loop for quick fixes",
  "type": "anti_pattern",
  "base_confidence": 0.6,
  "source": "seed",
  "evidence": "[]",
  "first_seen": "2026-03-30T22:14:59.343870+00:00",
  "last_fired": null,
  "fire_count": 0,
  "success_count": 0,
  "fail_count": 0,
  ...
}
```

Use `vidya items` to find item IDs to inspect.

---

## MCP Tool Reference

The MCP server exposes 7 tools, all accessible to any MCP client (Claude Code, etc.). Start the server with:

```bash
uv run --directory /path/to/vidya vidya-server
```

For Claude Code, register it in `~/.claude.json`:

```json
"vidya": {
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--directory", "/path/to/vidya", "vidya-server"]
}
```

---

### `vidya_start_task`

Start a task and receive relevant knowledge for that context. Call this at the beginning of significant work.

**Input:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `goal` | yes | What you're trying to accomplish |
| `language` | yes | Language scope |
| `goal_type` | no | `create`, `modify`, `debug`, `investigate`, `deploy`, `configure`, `review`, `refactor` |
| `runtime` | no | Runtime scope |
| `framework` | no | Framework scope |
| `project` | no | Project scope |

**Output:**

```json
{
  "task_id": "uuid",
  "knowledge": [
    {
      "id": "uuid",
      "pattern": "Always run full test suite before",
      "guidance": "Always run the full test suite before recording a task complete",
      "type": "precondition",
      "effective_confidence": 0.6,
      "scope_level": "project",
      "match_reason": "scope=project, language=python, project=canon, fts_match=1.00"
    }
  ]
}
```

Save the `task_id` — pass it to `vidya_end_task` and optionally to `vidya_feedback`.

---

### `vidya_end_task`

Mark a task complete and record its outcome.

**Input:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `task_id` | yes | From `vidya_start_task` |
| `outcome` | yes | `success`, `partial`, `failure`, `abandoned` |
| `outcome_detail` | no | Free-text description |
| `failure_type` | no | `incomplete`, `constraint_violation`, `wrong_result`, `tool_error`, `hallucination`, `off_topic` |

**Output:** `{"ok": true}`

---

### `vidya_record_step`

Record a decision or action taken during a task. Optional — call it when you want to log non-obvious choices for later extraction.

**Input:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `task_id` | yes | From `vidya_start_task` |
| `action` | yes | What you did |
| `result` | yes | What happened |
| `outcome` | yes | `success`, `error`, `rejected` |
| `rationale` | no | Why you chose this approach |
| `alternatives` | no | Array of strings — other approaches considered |

**Output:**

```json
{
  "step_id": "uuid",
  "matched_items": [
    {"id": "uuid", "pattern": "...", "guidance": "..."}
  ]
}
```

`matched_items` shows existing knowledge relevant to this step. Use it to check if Vidya already knows about what you just did.

---

### `vidya_query`

Query knowledge items for a given context. Use this mid-task when you need to check what Vidya knows about a specific operation.

**Input:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `context` | yes | — | What you're doing |
| `language` | yes | — | Language scope |
| `runtime` | no | — | Runtime scope |
| `framework` | no | — | Framework scope |
| `project` | no | — | Project scope |
| `goal` | no | — | Additional FTS terms |
| `min_confidence` | no | 0.2 | Exclude items below this effective confidence |

**Output:**

```json
{
  "items": [
    {
      "id": "uuid",
      "pattern": "...",
      "guidance": "...",
      "type": "convention",
      "effective_confidence": 0.6,
      "scope_level": "project",
      "match_reason": "scope=project, language=python, fts_match=1.00"
    }
  ]
}
```

---

### `vidya_feedback`

Record feedback and trigger knowledge extraction or confidence updates.

**Input:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `feedback_type` | yes | `review_accepted`, `review_rejected`, `test_passed`, `test_failed`, `user_correction`, `user_confirmation` |
| `detail` | yes | The correction text, confirmation, or test result detail |
| `task_id` | no | Link to the active task |
| `step_id` | no | Link to a specific step |
| `language` | no | Scope for the feedback |
| `runtime` | no | Scope |
| `framework` | no | Scope |
| `project` | no | Scope |

**Output:**

```json
{
  "feedback_id": "uuid",
  "learning": {"item_id": "uuid"}
}
```

Or if merged into an existing item:

```json
{
  "feedback_id": "uuid",
  "learning": {"merged": true, "item_id": "uuid"}
}
```

Or if no new item was created (positive/failure feedback updated existing items):

```json
{
  "feedback_id": "uuid",
  "learning": null
}
```

---

### `vidya_explain`

Retrieve the full record for a knowledge item, plus any items that override it.

**Input:** `{"item_id": "uuid"}`

**Output:**

```json
{
  "item": { ...full knowledge_items row as dict... },
  "overridden_by": [
    {"id": "uuid", "pattern": "...", "guidance": "..."}
  ]
}
```

`overridden_by` is the list of active items whose `overrides` field points to this item. Empty array if nothing overrides it.

---

### `vidya_stats`

Get knowledge base statistics.

**Input:** `{"language": "python", "project": "canon"}` (both optional)

**Output:**

```json
{
  "total_items": 22,
  "by_confidence": {"high": 22, "medium": 0, "low": 0},
  "by_type": {"convention": 8, "anti_pattern": 10, "postcondition": 3, "precondition": 1},
  "by_scope": {"global": 0, "language": 0, "runtime": 0, "framework": 0, "project": 22},
  "total_tasks": 0,
  "total_feedback": 0,
  "total_candidates": 0
}
```

---

## Seed File Format

`vidya seed` reads markdown and extracts actionable rules. Write seed files as focused rule lists, not raw documentation dumps.

### What gets extracted

A line becomes a knowledge item if it:

- Is a **bullet point or numbered list item** whose text starts with an imperative verb or contains "always", "never", "avoid", "don't", "must", "should", "prefer"
- Is a **standalone line** starting with an imperative verb (`use`, `avoid`, `always`, `never`, `prefer`, `ensure`, `run`, `add`, `write`, `don't`, `do not`, `check`, `keep`, `make`, `follow`, `put`, `place`, `define`, `set`, `return`, `raise`, `handle`, `log`, `test`, `import`, `require`, `enable`, `disable`, `configure`, `call`, `pass`, `store`, `save`, `load`, `read`) **and is longer than 10 characters**

Section headers (`#`) are ignored. Only bullet content matters.

### What does NOT get extracted

- Explanatory paragraphs
- Bullet/list items shorter than 8 characters
- Standalone lines of 10 characters or fewer
- Lines without an imperative signal

### Effective seed file structure

```markdown
# Section headers are ignored — use them for your own organization

## Testing

- Always run `uv run python -m pytest` not bare pytest
- Never commit with failing tests
- Always run the full test suite before marking a task complete

## Error handling

- Never use bare `except Exception` — catch specific exceptions
- Always use type hints on public function signatures
```

Each bullet becomes one knowledge item. All items in the file receive the same scope (`--language`, `--project`, etc.) and the same `--confidence`.

### Pattern derivation

The `pattern` field (used for FTS matching and display) is derived automatically: the first 6 non-stopword tokens of the rule text. For the rule "Never use bare `except Exception` — catch specific exceptions", the pattern becomes "Never use bare except Exception catch".

You cannot set the pattern explicitly via seed. Edit the DB directly if you need precise patterns.

### Deduplication at seed time

Before inserting each rule, Vidya runs FTS5 to find existing items with overlapping text. If the fraction of new-rule tokens found in an existing item exceeds 0.5, the rule is skipped. Re-seeding the same file is safe.

---

## Learning: How Feedback Works

Feedback is the primary learning mechanism in Phase 1. Two paths:

### Path 1: Negative feedback → new item or merge

Triggered by `user_correction` or `review_rejected`.

1. FTS5 searches for existing items that overlap with the feedback detail text.
2. For each candidate, compute overlap score: fraction of new-feedback tokens found in existing item's guidance + pattern.
3. If any existing item scores ≥ 0.5: **merge** — boost its confidence with `update_on_success`, append the feedback ID to its evidence list.
4. If no existing item reaches 0.5: **create** — generate an `extraction_candidate` with `initial_confidence=0.15`, then immediately promote it to `knowledge_items`.

New items from feedback start at `base_confidence=0.15` — low, because they come from a single observation. They build confidence through subsequent confirmations.

### Path 2: Positive feedback → confidence boost

Triggered by `user_confirmation` or `review_accepted`.

1. FTS5 finds existing items with > 0.3 overlap with the feedback text.
2. `update_on_success` is applied to each: `base_confidence += 0.05 × (1 − base_confidence)`, `last_fired = now`.

### Path 3: Test failure → confidence decay

Triggered by `test_failed`.

1. FTS5 finds existing items with > 0.3 overlap.
2. `update_on_failure` is applied: `base_confidence ×= 0.70`, `last_fired = now`.

### Type classification

Type is inferred automatically from the detail text by keyword detection:

| Keywords in text | Assigned type |
|-----------------|---------------|
| "don't", "never", "avoid" | `anti_pattern` |
| "after", "then", "verify" | `postcondition` |
| "before", "first", "ensure" | `precondition` |
| "always", "must", "should" (or none of the above) | `convention` |

---

## Database Tables

All tables live in `~/.vidya/vidya.db` (WAL mode, foreign keys enforced).

| Table | Purpose |
|-------|---------|
| `knowledge_items` | Active knowledge store. The primary table. |
| `knowledge_fts` | FTS5 virtual table synced to knowledge_items via triggers |
| `task_records` | One row per `vidya_start_task` call |
| `step_records` | One row per `vidya_record_step` call |
| `feedback_records` | One row per `vidya_feedback` call |
| `extraction_candidates` | Staging area before promotion to knowledge_items |
| `knowledge_archive` | Cold storage for archived/evicted items (JSON snapshots) |

### FTS5 sync

The `knowledge_fts` table is kept in sync with `knowledge_items` by three SQLite triggers:

- `knowledge_items_ai` — INSERT → insert into FTS
- `knowledge_items_ad` — DELETE → delete from FTS
- `knowledge_items_au` — UPDATE of `pattern`, `guidance`, or `explanation` → delete + re-insert in FTS

### Inspecting the database directly

```bash
sqlite3 ~/.vidya/vidya.db
```

```sql
-- Most useful queries
SELECT id, pattern, guidance, base_confidence, source FROM knowledge_items
  WHERE status = 'active' AND project = 'canon'
  ORDER BY base_confidence DESC;

SELECT feedback_type, detail, timestamp FROM feedback_records ORDER BY timestamp DESC LIMIT 10;

SELECT COUNT(*) FROM knowledge_items WHERE status = 'active';
```
