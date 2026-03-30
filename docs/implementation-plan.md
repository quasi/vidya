---
title: "Vidya Implementation Plan"
version: "1.0"
date: 2026-03-31
status: ready to execute
---

# Vidya Implementation Plan

---

## Phase 1: The Learning MVP

**Goal**: Vidya records observations, serves knowledge, and learns from feedback. Usable with Claude Code from day one.

**Target**: Code conventions in Canon project (Python/FastAPI/SQLAlchemy).

**Budget**: ~800 lines Python + ~100 lines tests.

### Step 1: Project Scaffolding

```
vidya/
    pyproject.toml        UV-managed, entry points for MCP server + CLI
    src/vidya/
        __init__.py
        schema.py
        store.py
        query.py
        learn.py
        maintain.py
        confidence.py
        seed.py
        mcp_server.py
        cli.py
    tests/
        test_confidence.py
        test_query.py
        test_learn.py
```

**pyproject.toml dependencies**:
- `mcp` (MCP server SDK)
- `click` (CLI)
- `tomli` (config parsing, if needed)

No other dependencies. SQLite is stdlib. JSON is stdlib. UUID is stdlib.

**Deliverable**: `uv run vidya-server` starts. `uv run vidya` prints help. Database created at `~/.vidya/vidya.db`.

### Step 2: Schema + Store

Implement `schema.py` and `store.py`.

**schema.py**:
- `init_db(path) -> Connection` — create tables, indexes, FTS5, enable WAL + FK
- All DDL from design document Section IV

**store.py** — CRUD for all tables:
- `create_task(goal, goal_type, language, ...) -> task_id`
- `end_task(task_id, outcome, outcome_detail, failure_type)`
- `create_step(task_id, action_type, action_name, ...) -> step_id`
- `create_feedback(feedback_type, detail, ...) -> feedback_id`
- `create_item(pattern, guidance, type, scope, ...) -> item_id`
- `update_item(item_id, **fields)`
- `get_item(item_id) -> KnowledgeItem`
- `create_candidate(pattern, guidance, type, scope, method, evidence) -> candidate_id`
- `promote_candidate(candidate_id) -> item_id` (candidate → knowledge item)
- `archive_item(item_id, reason)`

All functions take a `db: Connection` parameter. No global state.

**Test**: `test_store.py` — create, read, update cycle for each table.

### Step 3: Confidence Module

Implement `confidence.py`.

```python
TRUST_GROWTH = 0.05
TRUST_DECAY = 0.70
FRESHNESS_DECAY_RATE = 0.005
FRESHNESS_FLOOR = 0.3

def update_on_success(item) -> None
def update_on_failure(item) -> None
def compute_freshness(last_fired: str | None) -> float
def effective_confidence(base_confidence: float, freshness: float) -> float
```

**Test**: `test_confidence.py` — the three critical paths:
1. 20 successes from 0.0 → verify approaches but never reaches 1.0
2. One failure from 0.64 → verify drops to ~0.448, recovery takes ~8 successes
3. Freshness decay over 0/30/90/140/200 days → verify floor at 0.3

### Step 4: Cascade Query

Implement `query.py`.

```python
def cascade_query(
    db: Connection,
    context: str,
    language: str,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    goal: str | None = None,
    min_confidence: float = 0.2,
) -> list[QueryResult]
```

**Algorithm**:
1. Query active items matching any applicable scope level (global, language, runtime, framework, project)
2. Compute `effective_confidence = base_confidence * freshness` for each
3. Filter by `min_confidence`
4. FTS5 match on `context` and `goal` for relevance ranking
5. Score = `relevance * effective_confidence * scope_specificity_boost`
6. Scope specificity boost: project=1.5, framework=1.3, runtime=1.1, language=1.0, global=0.9
7. Sort by score descending
8. Build `match_reason` for each result
9. Handle overrides: if item A overrides item B and both match, suppress B

**Test**: `test_query.py` —
1. Global item + language item + project item on same topic → project wins
2. Override chain: project overrides language → language suppressed
3. FTS5 relevance: "error handling" matches "error recovery" but not "database migration"
4. Freshness affects ranking: stale item ranks below fresh item at same base_confidence

### Step 5: Feedback-Driven Extraction

Implement `learn.py`.

```python
def extract_from_feedback(
    db: Connection,
    feedback: FeedbackRecord,
    task: TaskRecord | None = None,
) -> ExtractionCandidate | None
```

**Algorithm**:
1. If `feedback_type` in (`review_rejected`, `user_correction`):
   a. Search FTS5 for existing items matching feedback detail
   b. If >80% overlap with existing item → update evidence, boost confidence, return None
   c. Else → create candidate with `initial_confidence=0.15`, `method='feedback'`
   d. Auto-promote candidate to knowledge item (feedback is high-quality signal)
2. If `feedback_type` in (`review_accepted`, `user_confirmation`):
   a. Find matching active items via FTS5
   b. Call `update_on_success` for each match
3. If `feedback_type` in (`test_failed`):
   a. Find matching active items
   b. Call `update_on_failure` for each match

**Pattern inference** (simple heuristic for Phase 1):
- Pattern = task goal (if available) or feedback detail summary
- Scope = feedback scope fields, or fall back to task scope

**Type classification** (keyword heuristic):
- "don't", "never", "avoid" → `anti_pattern`
- "always", "must", "should" → `convention`
- "before", "first", "ensure" → `precondition`
- "after", "then", "verify" → `postcondition`
- Default → `convention`

**Test**: `test_learn.py` —
1. `review_rejected` creates a new knowledge item
2. Second `review_rejected` on same topic merges evidence, doesn't duplicate
3. `review_accepted` boosts confidence on matching item
4. `test_failed` decreases confidence on matching item

### Step 6: Seed Import

Implement `seed.py`.

```python
def seed_from_file(
    db: Connection,
    file_path: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    base_confidence: float = 0.5,
) -> int  # number of items created
```

**Algorithm**:
1. Read the file (markdown)
2. Extract rules/guidelines (lines starting with imperative verbs, bullet points with "always/never/use/avoid")
3. For each extracted rule, create a knowledge item with source='seed'
4. Dedup against existing items via FTS5

This is intentionally simple. Manual curation of seed files is expected — Baba prepares a focused seed file, not a raw CLAUDE.md dump.

**CLI command**: `vidya seed --file rules.md --language python --project canon`

### Step 7: Maintenance

Implement `maintain.py`.

Phase 1 scope (minimal):

```python
def compute_stats(db: Connection, language=None, project=None) -> Stats
```

Freshness is computed at query time (Step 4), not batch-updated. Capacity eviction and drift detection are Phase 2.

### Step 8: MCP Server

Implement `mcp_server.py`.

Thin wrapper over the library. Each MCP tool maps to 1-3 library calls:

| MCP Tool | Library Calls |
|----------|--------------|
| `vidya_start_task` | `store.create_task()` + `query.cascade_query()` |
| `vidya_end_task` | `store.end_task()` |
| `vidya_record_step` | `store.create_step()` + `query.cascade_query()` (for matched_items) |
| `vidya_query` | `query.cascade_query()` |
| `vidya_feedback` | `store.create_feedback()` + `learn.extract_from_feedback()` |
| `vidya_explain` | `store.get_item()` + evidence/history queries |
| `vidya_stats` | `maintain.compute_stats()` |

**Entry point**: `vidya-server` (configured in pyproject.toml).

### Step 9: CLI Tool

Implement `cli.py` with Click.

```
vidya query --language python --project canon --context "error handling"
vidya stats [--language LANG] [--project PROJ]
vidya explain --item-id ID
vidya seed --file FILE --language LANG [--runtime RT] [--framework FW] [--project PROJ]
vidya feedback --type review_rejected --detail "..." --language LANG [--project PROJ]
vidya items [--language LANG] [--project PROJ] [--min-confidence 0.3]
vidya maintain   # (Phase 2: run decay, eviction, drift detection)
```

**Entry point**: `vidya` (configured in pyproject.toml).

### Step 10: Claude Code Integration

1. Add MCP server to `~/.claude.json`
2. Prepare seed file for Canon project (selective rules from Canon's CLAUDE.md)
3. Run `vidya seed` to populate initial knowledge
4. Add Vidya snippet to Canon's `CLAUDE.md`
5. Start working — observe first-session experience

### Phase 1 Validation Milestone

After 10 sessions with Canon:
- [ ] `vidya_query` returns ≥5 relevant items for common tasks
- [ ] At least 1 item was created by `vidya_feedback` (learning happened)
- [ ] `vidya_explain` shows evidence trail for seed and learned items
- [ ] `vidya stats` shows meaningful distribution
- [ ] Agent follows HIGH-confidence items without prompting
- [ ] Baba can read items in SQLite directly and they make sense

---

## Phase 2: Extraction + Maintenance

**Goal**: Vidya learns automatically from patterns, not just explicit feedback. Knowledge lifecycle is complete.

**Budget**: ~400 additional lines.

### Deliverables

1. **Frequency-based extraction** — detect recurring action→outcome patterns across tasks
2. **Contrast-based extraction** — compare successful vs failed tasks to find distinguishing conditions
3. **Tag-aware querying** — filter by tags on top of cascade
4. **Capacity budgets + eviction** — scope-level item limits, evict lowest-value
5. **Time-based decay batch job** — `vidya maintain` updates freshness, flags items below threshold
6. **Environmental drift detection** — compare runtime/framework versions at session start
7. **Override semantics** — project item explicitly overrides language item
8. **`details_json`** structured payloads for knowledge items (type-specific fields)

### Validation Milestone

After 30 total sessions:
- [ ] Vidya has extracted items that weren't seeded (genuine autonomous learning)
- [ ] Decay has demoted items that haven't been used
- [ ] At least one override exists (project overrides language)
- [ ] Extraction precision ≥70% (manually review candidates)

---

## Phase 3: Sophistication

**Goal**: Richer extraction, semantic search, export capabilities.

**Budget**: ~300 additional lines.

### Deliverables

1. **LLM-powered abstraction-based extraction** — cross-task pattern finding via LLM review
2. **Embedding-based semantic search** — FTS5 first, sqlite-vec if insufficient
3. **Export mature items as SKILL.md** — `vidya export --language CL --min-confidence 0.85`
4. **Claude Code hooks** — PostToolUse for passive observation (Bash exit codes, test results)
5. **Refinement version history** — track how items evolved
6. **Inter-item relationship traversal** — `related_items` querying

### The Acid Test

Apply Vidya to a structurally different domain (CLI expertise or research workflows) **with zero changes to the core library**. If it works, the architecture is general. If it requires core changes, feed those changes back into the design.

---

## Phase 4: Ghost Bridge

**Goal**: Ghost agents connect to Vidya. Knowledge flows both ways.

### Deliverables

1. CLOS wrapper around Vidya MCP tools (Learning Ghost Mixin)
2. Direct SQLite access path for local Ghost agents
3. CLI Expertise System as a Vidya specialization
4. Cross-agent knowledge transfer validation

### Validation Milestone

- [ ] Ghost agent benefits from knowledge Claude Code accumulated
- [ ] Claude Code session benefits from knowledge Ghost agent accumulated
- [ ] CLI Expertise System's seven rule categories work within Vidya's type system

---

## Execution Notes

### TDD Loop

Every step follows TDD:
1. Write test for the critical path
2. Implement until test passes
3. Refactor if needed
4. Move to next step

### Complexity Budget

| Phase | Estimated Lines | Cumulative |
|-------|----------------|------------|
| Phase 1 | ~800 + ~100 tests | ~900 |
| Phase 2 | ~400 | ~1300 |
| Phase 3 | ~300 | ~1600 |

The 1500-line budget (excluding tests) is tight but achievable. If Phase 1 comes in under 800, we have more room for Phases 2-3.

### What NOT to Build

- No ORM (raw SQLite via stdlib `sqlite3`)
- No external vector DB (FTS5 first, sqlite-vec only if needed)
- No web UI (CLI + SQLite is the UI)
- No async (synchronous is fine for MCP stdio server)
- No config file system in Phase 1 (constants in code)
- No multi-user support
- No network access (everything local)
