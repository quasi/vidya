---
title: "Vidya Phase 2 Specification"
version: "0.1-draft"
date: 2026-03-31
status: open questions — needs Baba review before implementation
---

# Vidya Phase 2 Specification

Phase 2 goal: **Vidya learns automatically from patterns, not just explicit feedback. Knowledge lifecycle is complete.**

This document expands the 8-line feature list from `implementation-plan.md` into concrete specs. Each section ends with its open questions. Nothing is implemented until all open questions for that feature are resolved.

---

## Orientation: What Phase 1 Built

Phase 1 is read-write:
- **Write**: `vidya_feedback` is the only learning trigger. One correction → one candidate → promoted at 0.15 confidence.
- **Read**: `cascade_query` — FTS5 + scope specificity + freshness decay.

Phase 2 adds:
- **Passive write paths** (frequency, contrast extraction — no agent action required)
- **Lifecycle management** (decay batch job, eviction)
- **Environmental awareness** (drift detection)
- **Richer query** (tag-aware)
- **Explicit override semantics**
- **Structured payloads** (`details_json`)

---

## Feature 1: Frequency-Based Extraction

### What it does

Detect recurring action→outcome patterns across tasks and auto-create candidates when a pattern meets a count threshold.

### Data source

`step_records`. Every call to `vidya_record_step` deposits a row. Frequency extraction scans completed tasks for repeated `(action_name, result_status)` pairs within the same scope.

### Proposed algorithm

```python
def extract_frequency_candidates(db, scope, min_count=3, window_days=30):
    """
    1. For each (action_name, result_status='success') in step_records
       filtered to scope + window, count occurrences across distinct tasks.
    2. Any (action_name) with count >= min_count: inspect the `thought` and
       `rationale` fields to derive a pattern and guidance.
    3. Dedup against existing items (same FTS threshold as feedback extraction).
    4. Create ExtractionCandidate with method='frequency', confidence=0.10.
    """
```

### Trigger

Run at `vidya_end_task` for the task's scope. Keeps extraction incremental (no full-table scan on every query).

### Open questions

**OQ-F1: Unit of a "pattern"**
The current `step_records` schema stores `action_name` (free text, e.g., "run pytest"). Is that granular enough to group reliably? Two calls to "write tests" may mean completely different things. Options:
- Use `action_name` verbatim and accept noise
- Use FTS tokenization to cluster semantically similar `action_name` values
- Require agents to use structured `action_type + action_name` (tool_call, decision, etc.)

Which grouping unit is meaningful here?

**OQ-F2: Count threshold and window**
The plan says "recurring." How many occurrences, over what time window?
- `min_count=3` seems reasonable — single observation is noise
- `window_days=30` — do patterns outside 30 days matter?
- Should threshold scale by scope? (project-level patterns need fewer observations than global)

**OQ-F3: Scope generalization**
If the same action pattern recurs in two different projects with the same language, should extraction create a language-level item instead of two project-level items?
- Pro: correct scope assignment
- Con: requires cross-scope analysis, risk of false generalization

**OQ-F4: What becomes guidance?**
For feedback-driven extraction, the user's correction text IS the guidance. For frequency extraction, we only have `action_name + thought + rationale` from `step_records`. These are agent-written free text — quality varies. Options:
- Use `thought` / `rationale` field directly (may be empty or irrelevant)
- Generate guidance as `"When doing X, outcome was Y (seen N times)"` — mechanical, not rich
- Require an LLM call to synthesize guidance from N step records (deferred to Phase 3)

**OQ-F5: Dependency on agent behavior**
Frequency extraction only works if agents actually call `vidya_record_step`. Phase 1 usage shows this is optional and likely sparse. If frequency extraction produces nothing useful due to thin data, is it worth the complexity?

---

## Feature 2: Contrast-Based Extraction

### What it does

Compare successful and failed tasks with similar goals in the same scope. Conditions present in successes but absent in failures → candidate knowledge items.

### Proposed algorithm

```python
def extract_contrast_candidates(db, scope, min_pairs=2):
    """
    1. Fetch completed tasks for scope, partitioned by outcome (success vs failure).
    2. Group by goal similarity using FTS tokenization (OR-token overlap).
    3. For each (success_group, failure_group) pair with min_pairs samples:
       a. Compare step sequences: which actions appeared in successes but not failures?
       b. Compare feedback: which items were fire_count-updated in success tasks?
       c. Candidate = "When doing X, presence/absence of Y distinguishes success from failure"
    4. Create ExtractionCandidate with method='contrast', confidence=0.12.
    """
```

### Open questions

**OQ-C1: How to group "similar tasks"**
Two tasks are "about the same thing" if... what? Options:
- Same `goal_type` + same scope (too broad — any `modify` task matches any other)
- FTS token overlap on `goal` text (needs threshold — what overlap fraction?)
- Exact match on first N tokens of goal (brittle)

The grouping decision dominates quality. A bad grouping produces garbage candidates.

**OQ-C2: Minimum sample size**
With light usage, there may be 1 success and 1 failure on "similar" tasks. Is that enough signal? Options:
- `min_pairs=2` means 2 successes + 1 failure or 1 success + 2 failures
- Safer: `min_successes=2, min_failures=1` (or vice versa)
- Skip contrast extraction until N ≥ 5 total tasks per group

**OQ-C3: What constitutes a "distinguishing condition"**
In step sequence comparison, what counts as distinguishing?
- An action that appears in all successes and no failures
- An action that appears in >70% of successes and <30% of failures
- A specific feedback event (e.g., `test_passed`) present in successes

**OQ-C4: Step sequence vs feedback comparison**
The algorithm above compares step sequences. But step_records are only populated when agents call `vidya_record_step` (see OQ-F5). Feedback records are more reliably populated. Should contrast extraction use feedback instead?

**OQ-C5: Ordering relative to frequency extraction**
If both run at `vidya_end_task`, they could generate duplicate candidates from the same data. Does dedup in candidate admission handle this? Or should they run sequentially with dedup between them?

---

## Feature 3: Tag-Aware Querying

### What it does

Tags handle cross-cutting concerns that the scope hierarchy can't express — "Python + PostgreSQL", "FastAPI + async", "testing only". Phase 1 stored tags in a `tags TEXT DEFAULT '[]'` column. Phase 2 queries them.

### Schema

Already exists:
```sql
tags TEXT DEFAULT '[]'   -- JSON array, e.g. '["postgresql", "async", "testing"]'
```

### Proposed query integration

Tags narrow the cascade. A tagged query only returns items whose tags intersect the requested tags **or** items with no tags. Items with non-intersecting tags are excluded.

```python
def cascade_query(db, context, language, ..., tags=None):
    # existing FTS + scope filter
    # if tags provided:
    #   WHERE tags = '[]' OR (tags contains any requested tag)
    # tags = OR semantics (not AND): any overlap qualifies
```

### Open questions

**OQ-T1: Who sets tags on items?**
Tags need to get onto items somehow. Options:
- **Seed-time**: `vidya seed --tags postgresql,async` (explicit, human-curated)
- **Agent-set**: `vidya_feedback(..., tags=["postgresql"])` (agent annotates at feedback time)
- **Auto-inferred**: During seed, extract tags from markdown section headers
- **None**: Tags start empty, populated only by explicit annotation

Phase 1 didn't populate tags on the 22 seeded Canon items. They're all `'[]'`.

**OQ-T2: AND vs OR semantics**
If a query specifies `tags=["postgresql", "async"]`:
- OR: return items tagged with postgresql OR async (union)
- AND: return items tagged with postgresql AND async (intersection)

OR is more useful in practice (broader recall). AND is more precise. Which?

**OQ-T3: Tag normalization**
"PostgreSQL", "postgresql", "postgres" are the same thing. Do we normalize on write? Case-fold only? Canonical alias list?

**OQ-T4: Tags in vidya_start_task / vidya_query MCP tools**
Which MCP tools get a `tags` parameter? Presumably `vidya_query` and `vidya_start_task`. Does `vidya_feedback` also accept tags (to tag items being created/updated)?

**OQ-T5: Tag filtering vs tag ranking**
Should tags be a hard filter (exclude non-matching items) or a soft boost (ranked higher, not excluded)? The current cascade uses boosts (scope specificity × FTS score × freshness). Tags as a hard filter changes the semantics.

---

## Feature 4: Capacity Budgets + Eviction

### What it does

Prevent unbounded growth. When a scope's item count exceeds budget, evict the lowest-value item to `knowledge_archive`.

### Budgets (from design.md)

| Scope | Default Max |
|-------|-------------|
| Global | 50 items |
| Language / Runtime | 500 items |
| Framework | 200 items |
| Project | 100 items |

### Proposed eviction policy

Evict the item with lowest `effective_confidence` (base_confidence × freshness computed at eviction time). Status set to `'evicted'`, snapshot written to `knowledge_archive`.

```python
def evict_if_needed(db, scope):
    count = count_active_items(db, scope)
    budget = get_budget(scope)
    if count <= budget:
        return 0
    # Sort by effective_confidence ASC, evict count-budget items
    ...
```

### Open questions

**OQ-E1: Eviction trigger — eager vs lazy**
- **Eager**: check and evict on every `create_item`. Zero drift from budget. Extra work on every write.
- **Lazy**: check and evict during `vidya maintain` batch job. Budget can be exceeded between runs.

**OQ-E2: Eviction metric**
`effective_confidence` is the obvious choice, but consider:
- A recently-seeded item at 0.6 confidence but never fired might be more valuable than a decayed item that was once 0.8. `effective_confidence` handles this correctly.
- But `effective_confidence` at eviction time uses current freshness. If `vidya maintain` hasn't run recently, freshness is stale. Recompute freshness at eviction time? (Yes — this is what `compute_freshness(last_fired)` already does.)
- Should `fire_count` matter? An item fired 50 times and recently confirmed is arguably more valuable than its raw `effective_confidence` suggests.

**OQ-E3: Seed item protection**
Should items with `source='seed'` be protected from eviction? Arguments:
- Pro: seeded items represent explicit Baba input — don't discard without asking
- Con: if they're stale and low-confidence, they're wrong, not valuable

**OQ-E4: Budget configurability**
Should budgets be overridable via `~/.vidya/config.toml`? Or hard-coded in `maintain.py`? The design.md says "Phase 2+: config.toml" for user configuration.

**OQ-E5: Eviction reversibility**
`knowledge_archive` stores a full JSON snapshot. Can an evicted item be restored? Is there a `vidya restore` CLI command? Or is archive write-only?

---

## Feature 5: Time-Based Decay Batch Job (`vidya maintain`)

### What it does

Phase 1 computes freshness at query time — items just silently disappear from results as they decay. Phase 2 adds a batch job that surfaces items at risk and optionally archives those that have been dormant beyond a threshold.

### Current state

Freshness is **computed** (not stored). `effective_confidence = base_confidence × compute_freshness(days_since_last_fired)`. An item with `effective_confidence < 0.2` is excluded from normal queries but persists in the DB.

### Proposed `vidya maintain` behavior

```
$ vidya maintain
$ vidya maintain --dry-run        # report without archiving
$ vidya maintain --archive        # archive items below threshold
```

Report output:
```
Stale items (effective_confidence < 0.2): 3
  - [item_id] "Always use uv run pytest" (last fired: 45 days ago, eff=0.18)
  ...
Near-stale items (effective_confidence < 0.3): 7
Eviction candidates (budget exceeded): 0
```

### Open questions

**OQ-M1: What does `vidya maintain` actually change?**

Three models:
- **Report only**: Surface at-risk items. No mutations. User decides. (`--dry-run` is default)
- **Auto-archive**: Items below LOW threshold for >N days get archived. Destructive but keeps DB clean.
- **Confidence penalization**: `base_confidence *= decay_factor` (not freshness) for very long unfired items. Changes semantics — base_confidence was supposed to be purely Bayesian, not time-decayed.

The design spec says freshness handles temporal staleness while base_confidence is Bayesian only. Option C would violate that mandate.

**OQ-M2: Archive threshold**
If auto-archive is adopted: at what point does an item cross from "stale" to "archive"?
- `effective_confidence < 0.2` + unfired for >90 days?
- `freshness == FRESHNESS_FLOOR` (max decay reached) + `fire_count == 0`?
- Purely time-based: unfired for >180 days regardless of confidence?

**OQ-M3: `vidya_stats` `decay_pending` field**
The design.md `vidya_stats` output includes `decay_pending: integer`. What is this count? Items currently below LOW? Items that will fall below LOW within N days? Items flagged by `vidya maintain`?

**OQ-M4: Is `vidya maintain` a new MCP tool or CLI-only?**
Maintenance feels like a human/script operation, not an agent operation. CLI-only seems right. But if an agent wanted to run it at session start, it would need the MCP tool. Is that useful?

---

## Feature 6: Environmental Drift Detection

### What it does

At session start, compare the current runtime/framework versions against the last-recorded state. If changed, demote freshness of items that may be affected (those scoped to the changed runtime/framework).

### Proposed schema addition

A new table to record environment snapshots per task:

```sql
CREATE TABLE IF NOT EXISTS environment_snapshots (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES task_records(id),
    timestamp TEXT NOT NULL,
    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,
    versions TEXT NOT NULL    -- JSON: {"python": "3.12.4", "mcp": "1.5.0", ...}
);
```

Or alternatively: store a single "last known good" row per scope in a `config`-style table.

### Proposed detection flow

`vidya_start_task(language="python", runtime="cpython-3.13", ...)`:
1. Fetch last snapshot for `(language="python", project="canon")`.
2. Compare runtime string: `"cpython-3.12"` → `"cpython-3.13"` = CHANGED.
3. Find active items with `runtime = "cpython-3.12"` or `runtime IS NULL AND language = "python"`.
4. For affected items: set `last_fired = NULL` so freshness computes to `FRESHNESS_FLOOR`.
5. Return `drift_detected: true` with `affected_items` in `vidya_start_task` response.

### Open questions

**OQ-D1: What environment to capture?**
`vidya_start_task` receives `language`, `runtime`, `framework` as strings. These are agent-provided labels, not actual version strings. To detect drift you need versions. Options:
- Agent passes explicit version info: `runtime="cpython-3.13.0"` (requires discipline)
- Vidya reads environment directly on server start: `sys.version`, `importlib.metadata`
- A separate `vidya_snapshot_env(...)` tool the agent calls at session start

Currently the MCP server has no mechanism to read the client environment.

**OQ-D2: Scope of "affected items"**
When `python 3.12 → 3.13` drift is detected, which items are "affected"?
- All items with `runtime LIKE 'cpython%'`?
- All items with `language = 'python'`?
- Only items tagged with the changed runtime?

Without knowing WHICH items are version-sensitive, the demotion is either too broad (demote everything) or too narrow (demote nothing).

**OQ-D3: Freshness demotion mechanism**
Setting `last_fired = NULL` makes freshness compute to `FRESHNESS_FLOOR`. But `last_fired = NULL` already means "never fired" — which is used for new items to give them full freshness in Phase 1. These two uses conflict.

Options:
- Add a `freshness_override REAL` column (set to FLOOR on drift, NULL otherwise)
- Use a separate `drift_demoted_at TEXT` column — if set and recent, use FLOOR
- Reconsider: maybe drift just sets `last_fired` to drift detection timestamp (immediate decay starts now)

**OQ-D4: False positive risk**
A Python 3.12 → 3.12.5 patch bump should not demote items. A 3.12 → 3.13 minor bump might warrant caution. A 3.x → 4.0 major bump definitely does. Is version comparison semver-aware, or just string equality?

**OQ-D5: Trigger timing**
Design.md says "at session start" (i.e., `vidya_start_task`). But drift detection adds latency to what's supposed to be a fast query-returning call. Is that acceptable? Alternative: async task at `vidya_start_task`, result available via next `vidya_query`.

---

## Feature 7: Override Semantics

### What it does

Make explicit when a narrower-scope item overrides a broader-scope item on the same topic. Currently the cascade always returns the narrower-scope item when both match, but neither item "knows" about the other.

### Current state

- `overrides TEXT` column exists on `knowledge_items` (stores item ID)
- `vidya_explain` already queries for items where `overrides = <item_id>` (what overrides this)
- Cascade does NOT use `overrides` — it relies purely on scope specificity boosts

### Proposed semantics

An `overrides` link means:
1. When the overriding item is returned in a query, suppress the overridden item entirely (not just de-rank)
2. `vidya_explain` shows the chain clearly: "This item overrides [id]: [pattern]"
3. If the overriding item is evicted or archived, the suppression ends (overridden item resurfaces)

This is additive to the existing scope boost — it handles cases where two items are at the SAME scope level but one is more specific/recent.

### Open questions

**OQ-O1: How is an override created?**

Three models:

a) **Explicit by agent**: `vidya_feedback(..., overrides="<item_id>")` — agent must know the item ID to override. Requires prior `vidya_query` to discover the item.

b) **Explicit by human**: `vidya override --item <new_id> --overrides <old_id>` CLI command.

c) **Auto-detected**: During `create_item` or `candidate admission`, if FTS similarity to an existing item at a broader scope exceeds threshold (e.g., 0.7), auto-set `overrides`. Silently keeps the DB clean but may wrongly link items.

d) **Conflict-surfaced**: Auto-detect potential conflicts and surface them in `vidya_explain` / `vidya_stats`, but don't auto-set — let Baba decide.

**OQ-O2: Override vs scope hierarchy**
The cascade already returns the project-scoped item before the language-scoped item. When is an explicit `overrides` link needed beyond that?

Scenario: Two items at the SAME scope level but one is a more refined version of the other. E.g., two conventions for error handling in Python — one general, one specific to async contexts. Neither is narrower in scope. The specific one should win when tags match. This is the case where `overrides` adds value that scope hierarchy alone can't provide.

Is this the canonical use case? Or are there others?

**OQ-O3: One-to-one or one-to-many?**
`overrides TEXT` is a single item ID. Can a new item override multiple older items? E.g., one refined rule replacing three old partial rules. Options:
- Keep one-to-one (simple)
- Change to `overrides TEXT DEFAULT '[]'` (JSON array, one-to-many)

**OQ-O4: Cascade behavior with explicit override**
Currently: both items might appear in results (narrower scope ranked higher). With override: the overriding item is returned, the overridden item is EXCLUDED. Does this change the query signature? Does the caller know suppression happened?

---

## Feature 8: `details_json` Structured Payloads

### What it does

`details_json TEXT` column already exists (NULL in Phase 1). Phase 2 populates it for types that benefit from machine-readable structure beyond free-text `guidance`.

### Proposed schemas per type

```python
# convention
{
  "severity": "error" | "warn" | "info",  # how strongly to enforce
  "auto_checkable": bool                  # can a tool verify compliance?
}

# precondition
{
  "check": str,          # human-readable check ("Is WAL enabled?")
  "verify_command": str  # optional shell command to verify ("PRAGMA journal_mode")
}

# postcondition
{
  "check": str,
  "verify_command": str
}

# anti_pattern
{
  "example": str,        # what NOT to do (code snippet or description)
  "instead": str         # what to do instead (links to a convention item ID?)
}

# diagnostic
{
  "symptoms": [str],     # observable signals
  "likely_causes": [str],
  "resolution_steps": [str]
}

# heuristic
{
  "confidence_modifier": float,  # adjust effective_confidence when applying
  "applies_when": str            # additional condition text
}
```

### Open questions

**OQ-J1: Who populates `details_json`?**
Phase 1 created items via feedback extraction (free-text → heuristic type classification). `details_json` was left NULL. For Phase 2:
- Agent provides it via `vidya_feedback(..., details={"severity": "error"})`
- CLI seed supports it via a YAML front-matter in the seed file
- Auto-inferred from guidance text (fragile)
- Human via `vidya update-item` CLI command (doesn't exist yet)

**OQ-J2: Validation**
Should `details_json` be validated against a per-type schema? Options:
- No validation (accept anything, trust callers)
- Soft validation: warn if required fields missing, but accept
- Hard validation: reject write if schema mismatch (requires Pydantic or equivalent)

The "no ORM, stdlib only" constraint eliminates Pydantic from the library. Manual JSON schema validation is verbose.

**OQ-J3: Effect on query and ranking**
Does `details_json` affect query behavior?
- `severity="error"` items should rank above `severity="warn"` items?
- `auto_checkable=True` items could be returned with a verification flag?
- Or is `details_json` purely for display in `vidya_explain`?

**OQ-J4: Seed file format for details**
Current seed parser reads markdown bullet points. If details_json fields need populating at seed time, what's the syntax? YAML front-matter per bullet? A separate JSON sidecar file?

---

## Cross-Cutting: New MCP Tools?

Phase 1 has 7 tools. Phase 2 may need new ones. Known candidates:

| Tool | Purpose | Alternatively |
|------|---------|--------------|
| `vidya_maintain` | Trigger decay/eviction from agent | CLI-only |
| `vidya_tag` | Add tags to an item | `vidya_feedback` extension |
| `vidya_override` | Create override link | `vidya_feedback` extension |
| `vidya_snapshot_env` | Record environment versions | `vidya_start_task` extension |

**OQ-X1: Extend existing tools vs add new ones?**
The clean option is to add optional parameters to `vidya_feedback` (tags, overrides, details). But this makes `vidya_feedback` a catch-all mutation tool. A dedicated `vidya_update_item(item_id, ...)` might be cleaner.

---

## Cross-Cutting: Schema Migration

Phase 2 requires no new columns — `tags`, `details_json`, `overrides`, `superseded_by` all exist in Phase 1 schema. But:

**OQ-X2: `environment_snapshots` table**
Drift detection needs somewhere to store snapshots. This IS a new table. Does `init_db` (which runs DDL idempotently with `CREATE TABLE IF NOT EXISTS`) handle this cleanly? Yes — no migration needed, just add the table to `_DDL` in `schema.py`.

**OQ-X3: Extraction trigger integration point**
Frequency and contrast extraction run at `vidya_end_task`. Currently `end_task` just writes the outcome row and returns. The MCP `vidya_end_task` handler would need to call the new extraction functions. Is this acceptable latency? (Scanning step records could be slow for long tasks.)

---

## Cross-Cutting: Test Strategy

**OQ-X4: Frequency/contrast extraction testing**
These algorithms need historical data. How do tests simulate it?
- Fixtures that pre-populate `step_records` and `task_records` (straightforward)
- But the grouping/threshold logic is sensitive to the test data shape — tests need enough cases to be meaningful, not just "N=1 smoke test"

**OQ-X5: Drift detection testing**
Requires controlling the "current environment" reading. If Vidya reads `sys.version` directly, tests can't mock it without monkey-patching. Better: pass `current_versions: dict` as a parameter to the detection function (testable). But this means `vidya_start_task` must read the environment and pass it in — where does that reading happen?

---

## Implementation Order (Proposed)

Given dependencies between features:

1. **Tags** (F3) — enables annotating extracted items correctly. Lowest risk, no new algorithm.
2. **Override semantics** (F7) — clarifies cascade behavior before adding more items.
3. **`details_json`** (F8) — needed if extracted items should carry structured metadata.
4. **`vidya maintain` + eviction** (F4 + F5) — lifecycle before adding more sources.
5. **Frequency extraction** (F1) — adds new candidates, needs eviction to manage growth.
6. **Contrast extraction** (F2) — depends on F1 being done (dedup already in place).
7. **Drift detection** (F6) — most uncertain, do last.

This ordering is a proposal. Baba may reprioritize.

---

## Summary of Open Questions

| # | Feature | Question | Blocking? |
|---|---------|----------|-----------|
| OQ-F1 | Frequency | Unit of a pattern (action_name vs semantic cluster) | Yes |
| OQ-F2 | Frequency | Min count threshold and time window | Yes |
| OQ-F3 | Frequency | Scope generalization across projects | No |
| OQ-F4 | Frequency | Source of guidance text | Yes |
| OQ-F5 | Frequency | Risk of thin data from sparse step recording | No |
| OQ-C1 | Contrast | Task grouping mechanism | Yes |
| OQ-C2 | Contrast | Minimum sample size | Yes |
| OQ-C3 | Contrast | Definition of "distinguishing condition" | Yes |
| OQ-C4 | Contrast | Steps vs feedback as data source | Yes |
| OQ-C5 | Contrast | Dedup ordering with frequency extraction | No |
| OQ-T1 | Tags | Who sets tags on items | Yes |
| OQ-T2 | Tags | AND vs OR query semantics | Yes |
| OQ-T3 | Tags | Normalization policy | No |
| OQ-T4 | Tags | Which MCP tools get `tags` parameter | Yes |
| OQ-T5 | Tags | Hard filter vs soft boost | Yes |
| OQ-E1 | Eviction | Eager vs lazy trigger | Yes |
| OQ-E2 | Eviction | Eviction metric (confidence only vs fire_count) | No |
| OQ-E3 | Eviction | Seed item protection | No |
| OQ-E4 | Eviction | Budget configurability | No |
| OQ-E5 | Eviction | Archive reversibility | No |
| OQ-M1 | Maintain | What `vidya maintain` actually mutates | Yes |
| OQ-M2 | Maintain | Archive threshold definition | Yes |
| OQ-M3 | Maintain | `decay_pending` field definition | No |
| OQ-M4 | Maintain | MCP tool vs CLI-only | No |
| OQ-D1 | Drift | What environment to capture (who reads versions) | Yes |
| OQ-D2 | Drift | Which items are "affected" by a version change | Yes |
| OQ-D3 | Drift | Freshness demotion mechanism (conflicts with NULL=new) | Yes |
| OQ-D4 | Drift | Semver-aware comparison | No |
| OQ-D5 | Drift | Latency at `vidya_start_task` | No |
| OQ-O1 | Override | How overrides are created | Yes |
| OQ-O2 | Override | Use case beyond scope hierarchy | Yes |
| OQ-O3 | Override | One-to-one vs one-to-many | No |
| OQ-O4 | Override | Caller visibility of suppression | No |
| OQ-J1 | details_json | Who populates it | Yes |
| OQ-J2 | details_json | Validation policy | No |
| OQ-J3 | details_json | Effect on query/ranking | No |
| OQ-J4 | details_json | Seed file format for details | No |
| OQ-X1 | Cross-cut | Extend existing tools vs new MCP tools | No |
| OQ-X2 | Cross-cut | Environment snapshots table in DDL | No |
| OQ-X3 | Cross-cut | Extraction latency at vidya_end_task | No |
| OQ-X4 | Cross-cut | Test fixtures for frequency/contrast | No |
| OQ-X5 | Cross-cut | Testability of drift detection | Yes |
