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
8. [Knowledge Evolution](#knowledge-evolution)
9. [Database Tables](#database-tables)

---

## Knowledge Model

Each knowledge item answers two questions: **when does this apply** (pattern) and **what to do** (guidance).

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | TEXT | UUID, primary key |
| `pattern` | TEXT | When this item applies (semantic condition) |
| `guidance` | TEXT | What to do |
| `type` | TEXT | `convention`, `anti_pattern`, `precondition`, `postcondition`, `bundle` (see Item types below) |
| `language` | TEXT | Scope: `python`, `common-lisp`, etc. NULL = global |
| `runtime` | TEXT | Scope: `cpython-3.12`, `sbcl`, etc. NULL = any |
| `framework` | TEXT | Scope: `fastapi`, `asdf`, etc. NULL = any |
| `project` | TEXT | Scope: `canon`, `pooler`, etc. NULL = any project |
| `base_confidence` | REAL | Epistemic trust (0–1). Updated by Bayesian formula on observed outcomes. |
| `source` | TEXT | `seed`, `observation`, `extraction`, `user_correction`, `evolution` |
| `tags` | TEXT | JSON array of strings. Stored but not yet queried (Phase 2). |
| `details_json` | TEXT | Structured payload, NULL in Phase 1 |
| `overrides` | TEXT | Item ID this item overrides (suppresses in query results) |
| `status` | TEXT | `active`, `archived`, `evicted`, `superseded` |
| `first_seen` | TEXT | ISO 8601 timestamp |
| `last_fired` | TEXT | ISO 8601 timestamp of last successful query match. NULL if never fired. |
| `fire_count` | INTEGER | Times returned in query results |
| `success_count` | INTEGER | Times positively confirmed |
| `fail_count` | INTEGER | Times negatively confirmed |
| `evidence` | TEXT | JSON array of feedback/observation IDs |
| `explanation` | TEXT | Free-text explanation of why the item exists |
| `bundle_id` | TEXT | ID of the bundle item this item belongs to (NULL if not bundled) |
| `related_items` | TEXT | JSON array of source item IDs (set on bundle items) |

### Item types

| Type | Meaning | Origin |
|------|---------|--------|
| `convention` | What to do | Auto-classified from feedback/seed text |
| `anti_pattern` | What not to do | Auto-classified from feedback/seed text |
| `precondition` | Check before acting | Auto-classified from feedback/seed text |
| `postcondition` | Verify after acting | Auto-classified from feedback/seed text |
| `bundle` | Compound rule synthesized from a cluster | Created by `vidya evolve` + promote |

For the four auto-classified types, classification happens during `vidya feedback` and `vidya seed` based on keyword detection in the guidance text:

| Keywords in text | Assigned type |
|-----------------|---------------|
| "don't", "never", "avoid" | `anti_pattern` |
| "after", "then", "verify" | `postcondition` |
| "before", "first", "ensure" | `precondition` |
| anything else | `convention` |

Override with explicit `--type` is not yet exposed; edit the DB directly if needed.

---

## Confidence Model

Two separate numbers combine to produce a ranking score.

### base_confidence

Epistemic trust. Starting value depends on the source event:

| Source | Starting base_confidence |
|--------|--------------------------|
| `user_correction` | 0.85 |
| `user_confirmation` | 0.70 |
| `review_rejected` | 0.65 |
| `test_outcome` | 0.60 |
| `seed` | user-supplied `--confidence` (default 0.5) |
| `extraction` | 0.40 |

Updated by heuristic formulas:

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
vidya query --context TEXT [options]
```

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--language` | no | — | Language scope |
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

**Additive scope**: `--language` and `--project` parameters are additive — an item matches if it belongs to the specified language scope OR the specified project scope. This means a query with `--language python --project myproject` returns items scoped to Python (any project) as well as items scoped to myproject (any language), plus items scoped to both.

**Porter stemmer**: FTS5 uses the Porter stemmer tokenizer. "testing" matches "test", "worktrees" matches "worktree". Context terms are stemmed before matching.

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

**Output paths** (one of four):

```
Created new knowledge item: <uuid>        # new item, no existing overlap
Merged into existing item: <uuid>         # feedback merged into existing item
Updated existing items.                   # positive/failure feedback updated confidence
Bundle <uuid> decomposed.                 # bundle broken apart; re-run to target a source item
```

**Bundle decomposition**: if a `user_correction` matches a bundle item, Vidya decomposes the bundle — clearing `bundle_id` on all source items and setting the bundle's status to `superseded`. Re-run the same feedback to target the individual source item now that it is free.

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

### `vidya maintain`

Run maintenance: health check, stale item detection, optional archival.

```
vidya maintain [--language LANG] [--project PROJECT] [--archive] [--confirm]
```

| Option | Description |
|--------|-------------|
| `--archive` | Include archive recommendations for stale items |
| `--confirm` | Actually archive stale items (requires `--archive`) |

Without `--confirm`, `--archive` shows what would be archived (dry run). Stale items are those with effective confidence below the LOW threshold that have not fired in over 30 days.

---

### `vidya evolve`

Detect clusters of thematically related knowledge items and synthesize compound rules via LLM.

```
vidya evolve [--language LANG] [--framework FW] [--project PROJECT] [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--cluster-only` | false | Show clusters without synthesis |
| `--dry-run` | false | Synthesize candidates without persisting them |
| `--review` | false | Interactive review of pending candidates |
| `--model` | — | Override LLM model (litellm model string) |
| `--min-size` | 3 | Minimum cluster size |
| `--overlap-threshold` | 0.35 | Minimum pairwise token overlap to connect two items |
| `--min-cohesion` | 0.35 | Minimum average pairwise overlap to accept a cluster |

**Three modes:**

**1. Cluster detection only** (`--cluster-only`)

Shows detected clusters without calling the LLM.

```bash
vidya evolve --cluster-only --language python --project myproject
```

```
Found 2 cluster(s).

Cluster 1: 4 item(s), cohesion=0.48
  Scope:  {'language': 'python', 'framework': None, 'project': 'myproject'}
  Theme:  pytest run test uv
  - Always use uv run pytest not bare pytest
  - Run full test suite before marking task complete
  - ...
```

**2. Synthesis** (default, or `--dry-run`)

Calls the configured LLM to compress each cluster into one compound rule. Persists a pending `evolution_candidate` per cluster (omitted on `--dry-run`).

```bash
vidya evolve --language python --project myproject
```

```
Candidate synthesized:
  Pattern:  Always use uv run pytest for Python test execution
  Guidance: Always run `uv run pytest` (never bare pytest); run the full suite before marking any task complete.
  Theme:    pytest run test uv
  Cohesion: 0.48
  ID:       3f8a...
```

**3. Review** (`--review`)

Interactive review of pending candidates. For each candidate, shows synthesized rule, cohesion score, and source items, then prompts:

```
Action: [a]pprove  [e]dit  [r]eject  [s]kip  [q]uit
```

- `a` — promote to `bundle` knowledge item; source items tagged with `bundle_id`
- `e` — open `$EDITOR` to edit guidance, then promote
- `r` — reject; source items unchanged
- `s` — skip for now; candidate remains pending
- `q` — stop reviewing

**LLM configuration via environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDYA_EVOLVE_MODEL` | `openai/gemma-4-26b-a4b-it-4bit` | litellm model string |
| `VIDYA_EVOLVE_API_BASE` | `http://192.168.1.17:8099/v1` | OpenAI-compatible base URL |
| `VIDYA_EVOLVE_API_KEY` | `omlx-1234` | Bearer token |

For hosted models: set `VIDYA_EVOLVE_MODEL=claude-haiku-4-5` and leave `API_BASE`/`API_KEY` unset to use Anthropic's SDK path.

**Threshold tuning**: defaults suit knowledge bases of 100+ items. For smaller bases use `--min-size 2 --overlap-threshold 0.3 --min-cohesion 0.3`.

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

### Path 4: Bundle decomposition

Triggered by `user_correction` when the best-matching item is a `bundle`.

1. FTS5 finds the best-matching existing item.
2. If that item is a bundle (`type = 'bundle'`), Vidya decomposes it: clears `bundle_id` on all source items, sets the bundle to `status = 'superseded'`.
3. The feedback is not immediately applied to any source item — the caller must re-submit the same correction to now target the freed source item.

This prevents a correction meant for one source rule from being silently absorbed by its aggregate bundle.

### Type classification

Type is inferred automatically from the detail text by keyword detection:

| Keywords in text | Assigned type |
|-----------------|---------------|
| "don't", "never", "avoid" | `anti_pattern` |
| "after", "then", "verify" | `postcondition` |
| "before", "first", "ensure" | `precondition` |
| "always", "must", "should" (or none of the above) | `convention` |

---

## Knowledge Evolution

The `evolve` pipeline converts accumulated atomic rules into compound rules (bundles). It runs entirely offline except for the LLM synthesis step.

### Pipeline stages

```
1. detect_clusters   — group active items by vocabulary overlap within scope
2. synthesize_cluster — LLM compresses each cluster into one rule → evolution_candidate
3. promote_candidate — human review approves: candidate becomes a bundle item
   reject_candidate  — human review rejects: candidate marked rejected, sources untouched
```

### Cluster detection algorithm

1. Query active non-bundle items matching the scope filter.
2. Tokenize each item's `pattern + guidance` text (lowercase, strip punctuation).
3. Group items by exact scope triple `(language, framework, project)`.
4. For each pair within a scope group, compute **symmetric overlap**: `|A ∩ B| / min(|A|, |B|)`.
5. Build an adjacency graph: connect items with overlap ≥ `--overlap-threshold`.
6. Extract connected components (BFS).
7. Reject components with fewer than `--min-size` members or average pairwise overlap below `--min-cohesion`.
8. Compute theme tokens: tokens that appear in more than 50% of members.

**Why symmetric overlap**: the smaller set drives the denominator, so a short specific rule can still connect to a longer related rule. A directional measure would miss these pairs.

### Evolution candidates table

Pending candidates live in `evolution_candidates` with `status = 'pending'`. After review:

| Status | Meaning |
|--------|---------|
| `pending` | Awaiting human review |
| `promoted` | Approved; bundle item created |
| `rejected` | Rejected; source items untouched |

### Bundle items

A bundle item has `type = 'bundle'` and `source = 'evolution'`. Its `related_items` field holds a JSON array of source item IDs. Each source item gains a `bundle_id` pointing back to the bundle.

`base_confidence` of the bundle = average `base_confidence` of its source items at promotion time.

Bundles are excluded from cluster detection (`type != 'bundle'` filter) to avoid re-clustering noise.

### Decomposition

If a `user_correction` feedback matches a bundle, `learn.py` decomposes it before applying the correction. Decomposition:

1. Clears `bundle_id = NULL` on all source items.
2. Sets the bundle's `status = 'superseded'`.
3. Returns a decomposed signal — the caller must re-submit the correction to target the now-free source item.

---

## Database Tables

All tables live in `~/.vidya/vidya.db` (WAL mode, foreign keys enforced).

| Table | Purpose |
|-------|---------|
| `knowledge_items` | Active knowledge store. The primary table. |
| `knowledge_fts` | FTS5 virtual table synced to knowledge_items via triggers (Porter stemmer) |
| `task_records` | One row per `vidya_start_task` call |
| `step_records` | One row per `vidya_record_step` call |
| `feedback_records` | One row per `vidya_feedback` call |
| `extraction_candidates` | Staging area before promotion to knowledge_items |
| `evolution_candidates` | Synthesized cluster candidates pending human review |
| `schema_migrations` | Tracks applied schema migrations (idempotent) |
| `knowledge_archive` | Cold storage for archived/evicted items (JSON snapshots) |

### FTS5 sync

The `knowledge_fts` table uses the **Porter stemmer tokenizer** (`tokenize = "porter unicode61"`). This means stemmed forms match: "testing" → "test", "worktrees" → "worktree", "adding" → "add".

The table is kept in sync with `knowledge_items` by three SQLite triggers:

- `knowledge_items_ai` — INSERT → insert into FTS
- `knowledge_items_ad` — DELETE → delete from FTS
- `knowledge_items_au` — UPDATE of `pattern`, `guidance`, or `explanation` → delete + re-insert in FTS

### Schema migrations

Applied migrations are tracked in `schema_migrations`:

| Migration name | Tracked in `schema_migrations` | What it does |
|----------------|--------------------------------|--------------|
| `fts_porter` | yes | Rebuilds FTS index with Porter stemmer tokenizer |
| `add_evolution` | no (uses `IF NOT EXISTS`) | Adds `bundle_id` column and `evolution_candidates` table |

Migrations run automatically on `init_db`. `fts_porter` is tracked in `schema_migrations` for idempotency. `add_evolution` relies on SQLite `ALTER TABLE … IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` and is safe to re-run without tracking.

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
