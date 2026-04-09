---
title: "Implementation Plan: Knowledge Evolution"
type: plan
feature: knowledge-evolution
design_doc: 2026-04-10-knowledge-evolution-design.md
date: 2026-04-10
tags: [canon, plan, knowledge-evolution, evolve]
---

# Knowledge Evolution — Implementation Plan

## Overview

| Metric | Value |
|--------|-------|
| Estimated lines | ~250-300 (new code) + ~50 (schema + migration) + ~150 (tests) |
| New files | `src/vidya/evolve.py`, `tests/test_evolve.py` |
| Modified files | `src/vidya/schema.py`, `src/vidya/query.py`, `src/vidya/cli.py`, `src/vidya/store.py` |
| Dependencies | `litellm` or `anthropic` SDK for LLM synthesis (already in project?) |
| Risk | Medium — touches query presentation path |

## Pre-Implementation Checklist

- [ ] Verify LLM dependency: check if `litellm` or `anthropic` is already in `pyproject.toml`
- [ ] Run full test suite to establish green baseline
- [ ] Read `src/vidya/query.py` cascade_query fully — understand presentation path

## Tasks

### Task 1: Schema Changes (~30 lines)

**Files:** `src/vidya/schema.py`

1. Add `bundle_id TEXT` column to `knowledge_items` table DDL
2. Add index: `CREATE INDEX IF NOT EXISTS idx_bundle_id ON knowledge_items(bundle_id)`
3. Add `evolution_candidates` table DDL (see design doc schema)
4. Add migration function: `migrate_add_evolution(conn)` that runs `ALTER TABLE knowledge_items ADD COLUMN bundle_id TEXT` and creates the new table. Guard with `try/except` for idempotency.

**Test:** `test_schema_evolution_candidates_created`, `test_schema_bundle_id_column_exists`

**Verify:** `sqlite3 ~/.vidya/vidya.db ".schema knowledge_items"` shows `bundle_id`, `".tables"` shows `evolution_candidates`

### Task 2: Cluster Detection (~80 lines)

**File:** `src/vidya/evolve.py` (new)

```python
def detect_clusters(
    db: sqlite3.Connection,
    language: str | None = None,
    framework: str | None = None, 
    project: str | None = None,
    min_size: int = 3,
    overlap_threshold: float = 0.4,
    min_cohesion: float = 0.5,
) -> list[Cluster]:
```

1. Query active items matching scope filter
2. Tokenize each item's `pattern + " " + guidance` (lowercase, split on whitespace, strip punctuation)
3. For each pair in same scope triple, compute `len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))`
4. Build adjacency dict (item_id → set of connected item_ids where overlap ≥ threshold)
5. Extract connected components via BFS
6. For each component ≥ min_size: compute average pairwise overlap (cohesion)
7. Reject components with cohesion < min_cohesion
8. For passing components: compute centroid tokens (appear in >50% of members)
9. Return `Cluster` dataclass list

**Reuse:** `learn.py:overlap_score` logic exists but is directional. The pairwise version should be symmetric: `shared / min(a, b)`.

**Test:** `test_cluster_same_scope`, `test_cluster_scope_isolation`, `test_cluster_excludes_archived`, `test_cluster_cohesion_gate`

### Task 3: Compound Synthesis (~60 lines)

**File:** `src/vidya/evolve.py`

```python
def synthesize_cluster(
    cluster: Cluster,
    items: list[dict],
    model: str = "claude-haiku-4-5",
) -> EvolutionCandidate | None:
```

1. Build LLM prompt:
   - System: "You are a technical knowledge compiler. Given related rules, produce ONE compound rule. Preserve every concrete detail (flag names, function names, error messages). Do not generalize away specifics. Output JSON: {\"pattern\": \"max 15 words\", \"guidance\": \"compound rule, imperative voice\"}"
   - User: numbered list of each item's pattern + guidance
2. Call LLM via `litellm.completion()` or `anthropic` SDK
3. Parse response JSON
4. Quality check: if guidance word count < shortest source item word count → add review_notes warning
5. Persist to `evolution_candidates` table with status='pending'
6. Return candidate or None on LLM failure

**Error handling:** LLM unavailable → log warning, return None. JSON parse failure → retry once with "respond with valid JSON only" suffix. Second failure → skip.

**Test:** `test_synthesize_happy_path` (mock LLM), `test_synthesize_llm_unavailable`, `test_synthesize_short_output_flagged`

### Task 4: Evolution Lifecycle — Promotion (~50 lines)

**File:** `src/vidya/evolve.py`

```python
def promote_candidate(
    db: sqlite3.Connection,
    candidate_id: str,
    edited_guidance: str | None = None,
) -> str:  # returns bundle item ID
```

1. Read candidate from `evolution_candidates`
2. Compute `base_confidence` = average of source items' `base_confidence`
3. Create new knowledge item: `type='bundle'`, `source='evolution'`, `related_items=source_item_ids`, guidance from candidate (or `edited_guidance` if provided)
4. Update source items: `SET bundle_id = ?` for each source
5. Update candidate: `SET status = 'promoted'`
6. Return bundle item ID

```python
def reject_candidate(db: sqlite3.Connection, candidate_id: str) -> None:
```

Simply: `UPDATE evolution_candidates SET status = 'rejected' WHERE id = ?`

**Test:** `test_promote_creates_bundle`, `test_promote_tags_sources`, `test_promote_with_edit`, `test_reject_leaves_sources_unchanged`

### Task 5: Evolution Lifecycle — Decomposition (~40 lines)

**File:** `src/vidya/evolve.py`

```python
def decompose_bundle(
    db: sqlite3.Connection,
    bundle_id: str,
) -> list[str]:  # returns un-bundled source item IDs
```

1. Read bundle item, get `related_items`
2. Clear `bundle_id` on all source items: `UPDATE knowledge_items SET bundle_id = NULL WHERE bundle_id = ?`
3. Set bundle to `status = 'superseded'`
4. Return source item IDs (for human review of correction routing)

**Integration with learn.py:** Modify `extract_from_feedback` — when feedback targets a bundle item (type='bundle'), call `decompose_bundle` first, then present sources to caller for routing. Do NOT auto-route.

**Test:** `test_decompose_clears_bundle_id`, `test_decompose_supersedes_bundle`, `test_feedback_on_bundle_triggers_decomposition`

### Task 6: Query Presentation Grouping (~30 lines)

**File:** `src/vidya/query.py`

Modify the return path of `cascade_query`:
1. After FTS + scope ranking produces the result list, check for `bundle_id` on results
2. Group results sharing the same `bundle_id`
3. For each group: replace individual guidance texts with the bundle's guidance text
4. Add `match_source: "bundle"` and `bundle_member_count: N` to grouped results
5. Results without `bundle_id` pass through unchanged

**Important:** This is presentation-layer only. The FTS query itself is untouched. Retrieval surface preserved.

**Test:** `test_query_groups_bundled_items`, `test_query_ungrouped_items_unchanged`, `test_query_after_decomposition_no_grouping`

### Task 7: CLI Commands (~50 lines)

**File:** `src/vidya/cli.py`

Add `evolve` command group:

```
vidya evolve                    → full pipeline: cluster → synthesize → show candidates
vidya evolve --cluster-only     → show clusters without synthesis
vidya evolve --dry-run          → synthesize but don't persist
vidya evolve --review           → interactive review of pending candidates
```

Review flow (non-JSON mode):
```
Candidate 1 of 3 [cohesion: 0.72, 4 source items]
Theme: CCC scenario formatting rules

Synthesized rule:
  Pattern: CCC scenario strict formatting requirements
  Guidance: "CCC scenarios enforce strict formatting: always set
  --cluster immediately after creation, never use = in Given/When/Then
  text (use 'of' phrasing), and only pass/fail/skip are valid status
  values for ClauseResult."

Source items:
  1. [0.85] Every scenario needs --cluster set...
  2. [0.85] scenario.language_independent treats = as code...
  3. [0.85] ClauseResult.status only accepts pass/fail/skip...
  4. [0.85] canon create scenario does not set cluster...

Action: [a]pprove  [e]dit  [r]eject  [s]kip  [q]uit >
```

JSON mode: output candidates as JSON array for programmatic consumption.

**Test:** CLI integration tests via `click.testing.CliRunner`

### Task 8: Integration Tests (~50 lines)

**File:** `tests/test_evolve.py`

End-to-end test:
1. Seed 10 items (5 in one thematic group, 3 in another, 2 isolated)
2. Run `detect_clusters` → verify 2 clusters
3. Run `synthesize_cluster` (mocked LLM) → verify 2 candidates
4. Run `promote_candidate` on first → verify bundle created, sources tagged
5. Run `cascade_query` → verify grouped presentation
6. Simulate negative feedback on bundle → verify decomposition
7. Run `cascade_query` again → verify individual presentation restored

## Task Ordering

```
Task 1 (schema) ──────────────────────────────────────┐
                                                       │
Task 2 (clustering) ──┐                               │
                      ├── Task 3 (synthesis) ──┐      │
                      │                        ├── Task 7 (CLI)
Task 4 (promotion) ───┤                        │      │
                      ├── Task 5 (decompose) ──┘      │
Task 6 (query group) ─┘                               │
                                                       │
Task 8 (integration) ─────────────────────────────────┘
```

Tasks 1-6 can be built in sequence with unit tests at each step. Task 7 (CLI) depends on all of 2-5. Task 8 integrates everything.

Parallelizable pairs: Tasks 2+4 (both read items, neither modifies schema beyond Task 1). Tasks 5+6 (decomposition and query grouping are independent).

## Verification

After implementation:

1. `uv run python -m pytest tests/test_evolve.py -v` — all unit + integration tests pass
2. `uv run python -m pytest` — full suite still green (no regressions)
3. Manual smoke test: `vidya evolve --cluster-only` on real DB (~96 items) — verify clusters make sense
4. Manual smoke test: `vidya evolve --dry-run` — verify synthesis produces readable output
5. `canon --json check` on vidya project — no new CCC failures from implementation

## Drift Report

### What Was Actually Built

Complete Knowledge Evolution feature across 8 tasks: schema changes (bundle_id column, evolution_candidates table, migration), cluster detection via FTS5 token overlap, LLM-based compound synthesis via litellm, promotion/rejection lifecycle with atomic transactions, bundle decomposition with learn.py integration, query presentation grouping, CLI commands (evolve with --cluster-only/--dry-run/--review), and end-to-end integration tests. 188 tests total (39 new), 0 regressions.

### Deviations from Plan

| Planned | Actual | Reason | Spec Impact |
|---------|--------|--------|-------------|
| `synthesize_cluster(cluster, items, model)` — no db param, caller persists | `synthesize_cluster(cluster, items, db, model)` — persists internally | Simpler callers, atomic persistence. Dry-run uses delete-after-insert. | Task 8 integration test adapted; no external contract change |
| evolution_candidates stored in extraction_candidates with method='evolution' (early design) | Dedicated evolution_candidates table (design critique resolution) | Design critique finding #2 — incompatible promotion semantics | Clean separation, no mixed queue |
| decompose_bundle + learn.py: auto-route correction to sources | Return `{"decomposed": True, ...}` — caller routes | Plan explicitly says "do NOT auto-route" — correction is surfaced to user via CLI | CLI shows decomposition message and tells user to re-run |
| --dry-run: synthesize without persisting | Synthesize, persist, then delete candidate row | Avoids refactoring synthesize_cluster for a persist flag | Functional equivalent; minor WAL overhead |
| `_group_by_bundle` uses bundle's scope for scope_level | Uses first FTS-matched member's scope_level | Presentation convenience — member scope reflects query context | Minor; acceptable for V1 |

### New Edge Cases Discovered

1. **Cohesion threshold sensitivity**: Real item text required careful token density to exceed min_cohesion=0.5. The 5-item and 3-item test groups needed ~60% shared vocabulary to reliably pass the gate. Threshold tuning with real data (~96 items) is recommended.

2. **Bundle items in clustering**: Without exclusion, promoted bundles would re-cluster with their own source items (shared vocabulary). Fixed: `detect_clusters` now excludes `type != 'bundle'`.

3. **FTS noise floor and bundle_member_count**: `_group_by_bundle` counts only source items that scored above the FTS noise threshold for the specific query. A bundle of 5 items may report `bundle_member_count=3` if 2 items didn't match the query context. This is correct behavior (reflects what actually matched) but may confuse users expecting the full bundle size.

4. **LLM empty output**: The LLM can return valid JSON with empty pattern/guidance strings. Added guard to reject these.

5. **Review scope filtering gap**: The --review mode initially queried all pending candidates regardless of --language/--framework/--project flags. Fixed: scope filters now applied to candidate query.

### Decisions Made During Implementation

1. **litellm over anthropic SDK**: Chosen for model flexibility — VIDYA_EVOLVE_MODEL env var allows any litellm-supported model, not just Anthropic.

2. **Atomic promote_candidate**: Wrapped in `with db:` transaction with status pre-check (must be 'pending'). Prevents re-promotion creating duplicate bundles.

3. **reject_candidate raises on missing ID**: Consistency with promote_candidate error handling.

4. **Raw SQL for bulk bundle_id clear in decompose_bundle**: Single `UPDATE ... WHERE bundle_id = ?` instead of N `update_item` calls. More efficient and clearer intent.

5. **`promote_candidate` naming**: Both store.py and evolve.py have functions named `promote_candidate` (different candidate types). CLI uses import alias `promote_evolution_candidate` for clarity.

### Suggested Spec Updates

1. Add `type != 'bundle'` exclusion to cluster detection contract
2. Document `bundle_member_count` as "FTS-matched members" not "total bundle size"
3. Add empty-output guard to synthesis contract
4. Document that `synthesize_cluster` persists internally (signature includes `db`)
5. Add scope filtering to --review mode specification
