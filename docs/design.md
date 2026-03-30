---
title: "Vidya Design Document"
version: "1.0"
date: 2026-03-31
status: approved (post-council review)
---

# Vidya Design Document

*Vidya (विद्या): Agent-agnostic procedural learning system.*

---

## I. Purpose

Vidya observes agent work, extracts procedural knowledge, and serves it back to any agent (or human, or script) that asks. Knowledge lives in the store, not in the agent. Any agent can deposit. Any consumer can query. Knowledge survives agent migration.

### I.1 What Vidya IS

- An MCP server backed by SQLite
- A CLI tool for humans and scripts
- A Python library that both wrap
- A knowledge store scoped by language/runtime/framework/project
- A learning system with confidence, decay, and drift detection

### I.2 What Vidya is NOT

- A replacement for CLAUDE.md (Vidya augments static harness, doesn't replace it)
- A fine-tuning system (no weight updates — structured knowledge accumulation only)
- A multi-user collaboration platform (single-user, single-machine in V1)
- An agent framework (Vidya provides knowledge; the agent provides reasoning)

---

## II. Architecture

### II.1 One Library, Many Interfaces

Council mandate: exactly one Python library handles all business logic. No consumer writes raw SQL. No dual write paths.

```
                         ┌──────────────────────┐
                         │   vidya (library)     │
                         │                       │
                         │  store.py   — CRUD    │
                         │  query.py   — cascade │
                         │  learn.py   — extract │
                         │  maintain.py— decay   │
                         │  schema.py  — DDL     │
                         └──────────┬────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
     ┌────────┴────────┐  ┌────────┴────────┐  ┌─────────┴───────┐
     │  MCP Server      │  │  CLI Tool        │  │  Direct Import  │
     │  (mcp_server.py) │  │  (cli.py)        │  │  (Ghost/tests)  │
     │                  │  │                  │  │                 │
     │  Claude Code,    │  │  Humans,         │  │  Ghost CLOS,    │
     │  any MCP client  │  │  scripts, CI/CD  │  │  pytest         │
     └──────────────────┘  └──────────────────┘  └─────────────────┘
```

### II.2 Storage

Single SQLite database at `~/.vidya/vidya.db` with WAL mode for concurrent read access.

```
~/.vidya/
    vidya.db              Main database
    vidya.db-wal          WAL file
    vidya.db-shm          Shared memory file
    config.toml           User configuration (future — Phase 2+)
```

---

## III. Knowledge Scope Hierarchy

### III.1 The Cascade

Knowledge is scoped from broadest to narrowest. Queries cascade from most-specific to broadest; most-specific applicable item wins.

```
GLOBAL                 ← universal preferences (NULL in all scope fields)
    │                    "Always TDD", "Never git add -A"
    │
    LANGUAGE             ← language-level conventions
    │                    "Use conditions/restarts for recoverable errors" (CL)
    │
    RUNTIME              ← runtime-specific behavior
    │                    "SBCL save-lisp-and-die requires no open streams"
    │
    FRAMEWORK            ← framework conventions
    │                    "Pydantic models for request/response" (FastAPI)
    │
    PROJECT              ← project-specific overrides
                         "In canon, use SQLAlchemy Core not ORM"
```

**Query cascade order**: project → framework → runtime → language → global.

When items at different scopes match the same context, the narrowest scope wins. An item can explicitly declare `overrides: <item-id>` to indicate it replaces a broader-scope item on the same topic.

### III.2 Scope Representation

In the schema, scope is four nullable TEXT columns:

- `language`: `"common-lisp"`, `"python"`, `"javascript"`, etc.
- `runtime`: `"sbcl"`, `"cpython-3.12"`, `"node-22"`, etc.
- `framework`: `"asdf"`, `"fastapi"`, `"react"`, etc.
- `project`: `"pooler"`, `"canon"`, `"ghost"`, etc.

**Global scope**: all four fields NULL.
**Language scope**: language set, rest NULL.
**Full scope**: all four set.

### III.3 Tags for Cross-Cutting Concerns

The strict hierarchy breaks on combinatorial rules ("Python + PostgreSQL", "FastAPI + async workers"). Tags provide a non-hierarchical escape valve.

- `tags`: JSON array of strings (e.g., `["postgresql", "async", "testing"]`)
- Phase 1: stored but not queried
- Phase 2: tag-aware filtering on top of the cascade

Tags are WHERE-clause filters on the existing cascade, not a parallel hierarchy.

---

## IV. Data Model

### IV.1 KnowledgeItem

```sql
CREATE TABLE knowledge_items (
    id TEXT PRIMARY KEY,

    -- Scope (hierarchical, all nullable for global)
    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    -- Knowledge
    pattern TEXT NOT NULL,           -- When does this apply? (semantic condition)
    guidance TEXT NOT NULL,          -- What to do (free text in Phase 1)
    type TEXT NOT NULL,              -- convention|precondition|postcondition|recovery|
                                    -- workflow|anti_pattern|heuristic|diagnostic|warning
    details_json TEXT,               -- Structured payload (Phase 2+, NULL in Phase 1)
    tags TEXT DEFAULT '[]',          -- JSON array of strings (stored Phase 1, queried Phase 2)

    -- Confidence (two-field model per council mandate)
    base_confidence REAL DEFAULT 0.0,   -- Epistemic trust, Bayesian updated
    freshness REAL DEFAULT 1.0,         -- Temporal staleness, time-decayed
    -- effective_confidence = base_confidence * freshness (computed, not stored)

    -- Provenance
    source TEXT DEFAULT 'observation',  -- seed|observation|extraction|cross_agent|human
    evidence TEXT DEFAULT '[]',         -- JSON array of observation IDs
    counter_evidence TEXT DEFAULT '[]', -- JSON array of contradicting observation IDs

    -- Metrics
    first_seen TEXT NOT NULL,
    last_fired TEXT,
    fire_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,

    -- Relationships
    overrides TEXT,                     -- Item ID this overrides (broader scope)
    superseded_by TEXT,                 -- Item ID that replaced this
    related_items TEXT DEFAULT '[]',    -- JSON array (stored Phase 1, traversed Phase 2)
    version INTEGER DEFAULT 1,

    -- Explanation
    explanation TEXT,

    -- Status
    status TEXT DEFAULT 'active'       -- active|archived|evicted
);

CREATE INDEX idx_scope ON knowledge_items(language, runtime, framework, project);
CREATE INDEX idx_status_confidence ON knowledge_items(status, base_confidence);
CREATE INDEX idx_type ON knowledge_items(type);
CREATE INDEX idx_last_fired ON knowledge_items(last_fired);
```

### IV.2 TaskRecord

```sql
CREATE TABLE task_records (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    timestamp_start TEXT NOT NULL,
    timestamp_end TEXT,

    -- Goal
    goal TEXT NOT NULL,
    goal_type TEXT,                     -- create|modify|debug|investigate|
                                       -- deploy|configure|review|refactor

    -- Scope
    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    -- Outcome
    outcome TEXT,                       -- success|partial|failure|abandoned
    outcome_detail TEXT,
    failure_type TEXT,                  -- incomplete|constraint_violation|wrong_result|
                                       -- tool_error|hallucination|off_topic

    -- Economics
    total_steps INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0.0,
    wall_clock_ms INTEGER DEFAULT 0,

    -- Context
    files_touched TEXT DEFAULT '[]'     -- JSON array
);

CREATE INDEX idx_task_scope ON task_records(language, project);
CREATE INDEX idx_task_outcome ON task_records(outcome);
```

### IV.3 StepRecord

```sql
CREATE TABLE step_records (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_records(id),
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL,

    -- TAR core
    thought TEXT,
    action_type TEXT NOT NULL,          -- tool_call|decision|file_operation|command
    action_name TEXT NOT NULL,
    action_args TEXT,                   -- JSON

    result_status TEXT NOT NULL,        -- success|error|timeout|partial|rejected
    result_output TEXT,
    result_error TEXT,

    -- Decision context (sparse — only recorded when agent calls vidya_record_step)
    alternatives TEXT,                  -- JSON array of {description, reason_rejected}
    preconditions TEXT,                 -- JSON array of {description, passed, detail}
    postconditions TEXT,                -- JSON array of {description, passed, detail}

    -- Operational
    duration_ms INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,

    UNIQUE(task_id, sequence)
);

CREATE INDEX idx_step_task ON step_records(task_id, sequence);
```

### IV.4 FeedbackRecord

```sql
CREATE TABLE feedback_records (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES task_records(id),
    step_id TEXT REFERENCES step_records(id),
    timestamp TEXT NOT NULL,

    feedback_type TEXT NOT NULL,         -- review_accepted|review_rejected|
                                        -- test_passed|test_failed|
                                        -- user_correction|user_confirmation
    detail TEXT NOT NULL,

    -- Scope (may differ from task scope for language-level feedback)
    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    -- What happened as a result
    items_affected TEXT DEFAULT '[]'     -- JSON: [{item_id, action, old_conf, new_conf}]
);

CREATE INDEX idx_feedback_task ON feedback_records(task_id);
CREATE INDEX idx_feedback_type ON feedback_records(feedback_type);
```

### IV.5 ExtractionCandidate

```sql
CREATE TABLE extraction_candidates (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,

    pattern TEXT NOT NULL,
    guidance TEXT NOT NULL,
    type TEXT NOT NULL,

    language TEXT,
    runtime TEXT,
    framework TEXT,
    project TEXT,

    extraction_method TEXT NOT NULL,     -- feedback|frequency|contrast|abstraction
    evidence TEXT NOT NULL,              -- JSON array of observation/feedback IDs
    initial_confidence REAL DEFAULT 0.0,

    status TEXT DEFAULT 'pending',       -- pending|approved|rejected|merged
    merged_into TEXT,                    -- Item ID if merged into existing
    review_notes TEXT
);
```

### IV.6 Full-Text Search

```sql
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    pattern, guidance, explanation,
    content='knowledge_items',
    content_rowid='rowid'
);
```

### IV.7 Archive (Cold Storage)

```sql
CREATE TABLE knowledge_archive (
    id TEXT PRIMARY KEY,
    archived_at TEXT NOT NULL,
    reason TEXT NOT NULL,                -- evicted|superseded|manual
    original_data TEXT NOT NULL          -- JSON snapshot
);
```

---

## V. Confidence Model

### V.1 Two-Field Model

Council mandate: separate epistemic trust from temporal staleness.

- **`base_confidence`**: "Is this knowledge correct?" Updated by Bayesian formula on observed outcomes. Never decays autonomously.
- **`freshness`**: "Has this been tested recently?" Decays with calendar time since last firing. Recovers instantly when the item fires successfully.
- **`effective_confidence`**: `base_confidence * freshness`. Used for query ranking. Computed at query time, not stored.

### V.2 Bayesian Updating

```python
TRUST_GROWTH = 0.05   # alpha — slow to trust
TRUST_DECAY = 0.70    # beta — quick to doubt

def update_confidence(item, success: bool):
    if success:
        item.base_confidence += TRUST_GROWTH * (1.0 - item.base_confidence)
        item.freshness = 1.0  # reset freshness on use
    else:
        item.base_confidence *= TRUST_DECAY
        item.freshness = 1.0  # still recently tested
    item.last_fired = now()
    item.fire_count += 1
    if success:
        item.success_count += 1
    else:
        item.fail_count += 1
```

**Recovery ratio**: From `base_confidence=0.64`, one failure drops to `0.448`. Recovery to `0.64` requires ~8 successes. This is intentionally conservative — the system should be slow to trust.

### V.3 Freshness Decay

```python
FRESHNESS_DECAY_RATE = 0.005   # 0.5% per day
FRESHNESS_FLOOR = 0.3          # never fully forgotten

def compute_freshness(last_fired: datetime) -> float:
    if last_fired is None:
        return FRESHNESS_FLOOR
    days = (now() - last_fired).days
    return max(FRESHNESS_FLOOR, 1.0 - FRESHNESS_DECAY_RATE * days)
```

An item unfired for 140 days decays from freshness 1.0 to the floor (0.3). The base_confidence is preserved — one successful use restores full ranking.

### V.4 Confidence Bands

```
effective_confidence    Behavior
───────────────────     ─────────────────────────────────────
HIGH (> 0.5)            Returned in query results, high rank.
                        Agent should follow this guidance.

MEDIUM (0.2 - 0.5)     Returned in query results, lower rank.
                        Marked as "provisional." Agent decides.

LOW (< 0.2)            Not returned in normal queries.
                        Visible in vidya_explain and stats only.
```

Note: effective thresholds are `base_confidence * freshness`. A high-base item that's stale (freshness=0.3) might drop from HIGH to MEDIUM, prompting the agent to verify rather than blindly follow.

---

## VI. MCP Interface

### VI.1 Tools

#### `vidya_start_task`

```
Input:
  goal:       string (required)
  goal_type:  string (optional)   create|modify|debug|investigate|deploy|configure|review|refactor
  language:   string (required)
  runtime:    string (optional)
  framework:  string (optional)
  project:    string (optional)

Output:
  task_id:    string
  knowledge:  list<{
    id:                  string
    pattern:             string
    guidance:            string
    type:                string
    effective_confidence: float
    scope_level:         string    global|language|runtime|framework|project
    match_reason:        string    "Matched on language=python, context similarity to 'error handling'"
  }>
```

Internally: calls `store.create_task()` then `query.cascade_query()`. Combined for adoption convenience. Standalone `vidya_query` available for ad-hoc lookups.

#### `vidya_end_task`

```
Input:
  task_id:       string (required)
  outcome:       string (required)   success|partial|failure|abandoned
  outcome_detail: string (optional)
  failure_type:  string (optional)   incomplete|constraint_violation|wrong_result|
                                     tool_error|hallucination|off_topic

Output:
  items_updated: integer
  candidates:    integer
```

#### `vidya_record_step`

```
Input:
  task_id:      string (required)
  action:       string (required)    What the agent did
  result:       string (required)    What happened
  outcome:      string (required)    success|error|rejected
  alternatives: list<string> (opt)   Other approaches considered
  rationale:    string (optional)    Why this approach was chosen

Output:
  step_id:       string
  matched_items: list<KnowledgeItem>   Existing items relevant to this step
```

#### `vidya_query`

```
Input:
  context:        string (required)   Semantic description of what you're doing
  language:       string (required)
  runtime:        string (optional)
  framework:      string (optional)
  project:        string (optional)
  goal:           string (optional)
  min_confidence: float (optional)    Default: 0.2

Output:
  items: list<{
    id, pattern, guidance, type,
    effective_confidence, scope_level, match_reason
  }>
```

#### `vidya_feedback`

```
Input:
  task_id:       string (optional)
  step_id:       string (optional)
  feedback_type: string (required)    review_accepted|review_rejected|
                                      test_passed|test_failed|
                                      user_correction|user_confirmation
  detail:        string (required)
  language:      string (optional)    Override scope (for language-level feedback)
  runtime:       string (optional)
  framework:     string (optional)
  project:       string (optional)

Output:
  items_affected: list<{item_id, action, old_confidence, new_confidence}>
  candidates:     list<{id, pattern, guidance, type, confidence}>
```

**This is the primary learning trigger in Phase 1.** `review_rejected` and `user_correction` immediately generate a candidate knowledge item via feedback-driven extraction.

#### `vidya_explain`

```
Input:
  item_id: string (required)

Output:
  item:           Full KnowledgeItem
  evidence:       list<Observation>
  history:        list<{timestamp, event, old_value, new_value}>
  overrides:      KnowledgeItem?
  overridden_by:  list<KnowledgeItem>
```

#### `vidya_stats`

```
Input:
  language:  string (optional)
  project:   string (optional)

Output:
  total_items:      integer
  by_confidence:    {high: n, medium: n, low: n}
  by_type:          {convention: n, precondition: n, ...}
  by_scope:         {global: n, language: n, runtime: n, framework: n, project: n}
  total_observations: integer
  last_extraction:  ISO 8601
  decay_pending:    integer
```

---

## VII. Extraction Engine

### VII.1 Feedback-Driven Extraction (Phase 1)

The only extraction method in Phase 1. Triggered immediately by `vidya_feedback`.

```python
def extract_from_feedback(feedback: FeedbackRecord) -> Optional[ExtractionCandidate]:
    if feedback.feedback_type in ('review_rejected', 'user_correction'):
        # Check for existing item on same topic (dedup via FTS5)
        existing = search_fts(feedback.detail)
        if existing and similarity(existing, feedback) > 0.8:
            # Merge: update existing item's evidence, boost confidence
            update_evidence(existing, feedback)
            return None

        # New candidate
        return ExtractionCandidate(
            pattern=infer_pattern(feedback),     # What context triggered this?
            guidance=feedback.detail,             # The correction IS the guidance
            type=classify_feedback(feedback),     # convention? precondition? anti-pattern?
            scope=feedback.scope or task.scope,
            extraction_method='feedback',
            evidence=[feedback.id],
            initial_confidence=0.15,              # Low — single observation
        )

    elif feedback.feedback_type in ('review_accepted', 'user_confirmation'):
        # Boost matching items
        matching = find_matching_items(feedback)
        for item in matching:
            update_confidence(item, success=True)
        return None
```

**Pattern inference**: For Phase 1, the pattern is derived from the task context + feedback detail. E.g., feedback "use Result type not exceptions" on a task with goal "add error handling" in project "pooler" → pattern "error handling in pooler".

**Type classification**: Simple heuristic:
- Feedback contains "don't", "never", "avoid" → `anti_pattern`
- Feedback contains "always", "must", "should" → `convention`
- Feedback contains "before", "first", "ensure" → `precondition`
- Feedback contains "after", "then", "verify" → `postcondition`
- Default → `convention`

### VII.2 Frequency-Based Extraction (Phase 2)

Find recurring action→outcome patterns across tasks.

### VII.3 Contrast-Based Extraction (Phase 2)

Compare successful and failed tasks with similar goals to identify distinguishing conditions.

### VII.4 Candidate Admission

All candidates go through:

1. **Deduplication**: FTS5 search for semantic overlap with existing items. >80% overlap → merge evidence into existing item.
2. **Scope validation**: Is this truly the right scope level? A single-project observation shouldn't create a language-level item.
3. **Contradiction check**: Any existing items that contradict? If so, flag for scope narrowing.

In Phase 1, candidates from feedback are auto-admitted at low confidence (0.15). They prove themselves through subsequent feedback events.

---

## VIII. Maintenance Engine

### VIII.1 Freshness Decay

Applied at query time (computed, not stored batch-updated). When `effective_confidence` drops below LOW threshold, item stops appearing in normal queries but remains in the store.

### VIII.2 Capacity Budgets (Phase 2)

| Scope | Default Max |
|-------|-------------|
| Global | 50 items |
| Language/runtime | 500 items |
| Framework | 200 items |
| Project | 100 items |

When full, lowest `effective_confidence` item is evicted to `knowledge_archive`.

### VIII.3 Environmental Drift Detection (Phase 2)

At session start, compare current environment (runtime version, key dependency versions) against last recorded state. If changed, demote affected items' freshness to `FRESHNESS_FLOOR`.

---

## IX. Seed Strategy

### IX.1 Sources and Confidence

| Source | Initial `base_confidence` |
|--------|--------------------------|
| Baba's corrections/rules (CLAUDE.md) | 0.60 |
| Workflow documentation | 0.50 |
| QKW insights tagged with language | 0.40 |
| Auto-extracted from documentation | 0.30 |

### IX.2 Seeding Principles

1. **Selective, not bulk.** Only actionable, scoped, agent-agnostic rules.
2. **Scoped correctly.** "Never git add -A" is global. "Use conditions/restarts" is CL-language. "SQLAlchemy Core not ORM" is canon-project.
3. **Freshness = 1.0 on seed.** Seeded items start fresh — they haven't had a chance to be stale yet.
4. **Source = 'seed'.** Distinguishable from learned items. Can be re-seeded if the source file updates.

---

## X. Claude Code Integration

### X.1 MCP Configuration

```json
// ~/.claude.json
{
  "mcpServers": {
    "vidya": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/vidya", "vidya-server"]
    }
  }
}
```

### X.2 Project CLAUDE.md Snippet

```markdown
## Learning System (Vidya)

This project uses Vidya for accumulated knowledge. Follow this workflow:

1. At the start of significant tasks, call `vidya_start_task` with your goal
   — it returns relevant knowledge from past sessions. Follow HIGH-confidence items.
2. When making non-obvious decisions, call `vidya_record_step` with alternatives.
3. After receiving code review feedback or user corrections, call `vidya_feedback`.
4. Call `vidya_end_task` when done, with the outcome (success/partial/failure).
```

### X.3 Adoption Contract (Council Mandate)

Three moments that must work from session 1:

1. **`vidya_start_task`** returns obviously relevant seed knowledge
2. **`vidya_feedback`** visibly creates or updates an item after one correction
3. **`vidya_explain`** lets Baba inspect why any item exists

If these three fail, Vidya becomes furniture.

---

## XI. Module Structure

```
src/vidya/
    __init__.py          Package init, version
    schema.py            DDL, migrations, DB initialization
    store.py             CRUD for all tables (tasks, steps, feedback, items, candidates)
    query.py             Cascade query with scope resolution and match_reason
    learn.py             Extraction engine (feedback-driven Phase 1, frequency/contrast Phase 2)
    maintain.py          Freshness computation, capacity eviction, drift detection
    confidence.py        Bayesian update logic, constants
    seed.py              Import from CLAUDE.md files and other sources
    mcp_server.py        MCP tool definitions, thin wrapper over library
    cli.py               Click-based CLI, thin wrapper over library
```

Estimated Phase 1 size: ~800 lines (including ~100 lines tests).

---

## XII. Error Handling

### XII.1 Graceful Degradation

If Vidya is unavailable (MCP server not running, DB locked, etc.):
- Agent falls back to pure CLAUDE.md + LLM reasoning
- No worse than the status quo
- No data loss — observations are buffered and ingested when Vidya recovers

### XII.2 Data Integrity

- WAL mode for concurrent readers
- All writes through the single library (no raw SQL from consumers)
- Atomic transactions for confidence updates (read-modify-write in a transaction)
- Foreign keys enforced

---

## XIII. Future Extensions

Designed for but not built in Phase 1:

| Extension | Schema Support | Logic |
|-----------|---------------|-------|
| Tags for cross-cutting | `tags` JSON column | Phase 2 querying |
| Structured guidance | `details_json` column | Phase 2 payloads |
| Inter-item relationships | `related_items` JSON column | Phase 2 traversal |
| Capacity budgets | status='evicted' + archive table | Phase 2 eviction |
| Drift detection | Compare runtime versions at session start | Phase 2 maintenance |
| Embedding search | `embedding` BLOB column (sqlite-vec) | Phase 3 semantic match |
| Override chains | `overrides` column | Phase 2 cascade logic |
| Export to SKILL.md | Mature items → markdown | Phase 3 |
| Ghost bridge | Same MCP protocol or direct import | Phase 4 |
