---
title: "Design: Confidence Model Rework & Pipeline Fix"
type: design
generated_from: canon
feature: confidence-model, feedback-learning
canon_version: 0.1.0
date: 2026-04-06
revision: 2
tags: [canon, design, confidence-model, feedback-learning]
---

# Confidence Model Rework & Pipeline Fix — Design Document

## Purpose

Vidya's confidence model and learning pipeline have two fundamental flaws that make the system less useful than it should be:

1. **Freshness decay destroys valid knowledge.** Procedural knowledge (conventions, anti-patterns) doesn't rot with time, but the current model penalizes items by 0.5%/day just for not being used. After 140 days, any item hits the 0.3 floor. Result: 70% of items (55/79) are LOW confidence — not because they're wrong, but because time passed.

2. **The extraction pipeline silently drops learning signals.** Only user corrections create new items. Confirmations and test failures that don't match existing items are discarded. Of 167 feedback signals, 107 (64%) could only update existing items — new knowledge via these channels vanished.

These changes make Vidya's stored knowledge actually accessible when you return to a project after weeks or months.

## Scope

### In Scope

- Remove freshness decay from confidence computation
- Remove `effective_confidence` function entirely (identity wrapper is ceremony)
- Add source-based initial confidence (SOURCE_CONFIDENCE mapping with 6 source tiers)
- Fix extraction pipeline: all feedback types capture knowledge (corrections auto-promote, confirmations/failures create pending candidates)
- Add `diagnostic` knowledge item type for test failure learnings (already in schema)
- Migrate existing items to correct source and confidence values, preserving fire history
- Update `maintain` staleness criteria (evidence-based, not freshness-based)
- Update all callers: `query.py`, `maintain.py`, `brief.py`, `guidance.py`, `cli.py`
- Rename "Bayesian" to "heuristic" in code docs (exponential smoothing, not Bayesian)

### Out of Scope

- Semantic dedup via embeddings (Phase 3)
- LLM-assisted periodic review (COSMO supervisor pattern)
- Corroboration scoring from related items
- Richer provenance tracking (conversation ID, etc.)

## Domain Model

| Entity | Definition | Relationships |
|--------|-----------|---------------|
| SOURCE_CONFIDENCE | Dict mapping 6 source tiers to initial base_confidence | Used by feedback-extraction to set initial confidence |
| diagnostic | Knowledge item type for test-failure-derived items | Already in schema (`_VALID_ITEM_TYPES`) |
| base_confidence | Epistemic trust score (0-1), updated by heuristic formula | Used directly for ranking — no wrapper function |

## Behavioral Contracts

### confidence-update

Manages how confidence values are assigned and updated.

**Key interfaces:**
- `SOURCE_CONFIDENCE` dict:

| Source | Confidence | Rationale |
|--------|-----------|-----------|
| `user_correction` | 0.85 | Explicit human knowledge transfer with specific corrective detail. Not 1.0 because humans can be wrong. |
| `user_confirmation` | 0.70 | Explicit human validation, but weaker than correction — confirmations may be vague agreement without corrective detail. |
| `review_rejected` | 0.65 | Agent-initiated rejection. Lower than user correction because the agent may misinterpret feedback context. |
| `test_outcome` | 0.60 | Empirical but narrow — specific test, specific conditions. |
| `seed` | 0.60 | Manually curated but not battle-tested. |
| `extraction` | 0.40 | Unvalidated machine inference, awaiting confirmation. |

- `update_on_success(item)`: Heuristic growth — `base += 0.05 * (1 - base)`. Asymptotic, never reaches 1.0.
- `update_on_failure(item)`: Heuristic decay — `base *= 0.70`. Quick to doubt.
- No `effective_confidence` function. Callers use `base_confidence` directly.

**Guarantees:** Items never lose confidence from time alone. Only explicit outcome signals (success/failure) change base_confidence. Items with zero fires retain source-based confidence indefinitely.

**Error conditions:** Unknown source string raises KeyError from SOURCE_CONFIDENCE lookup.

### feedback-extraction

Routes feedback signals to create or update knowledge items. Two-tier promotion model: corrections auto-promote, everything else creates pending candidates.

**Key interfaces:**
- `extract_from_feedback(db, feedback, task?)`: Routes by feedback_type:

| Feedback type | Match found (overlap ≥ 0.3) | No match |
|---|---|---|
| `user_correction` | Merge if ≥ 0.5, else boost | **Auto-promote** new item at 0.85 |
| `review_rejected` | Merge if ≥ 0.5, else boost | **Auto-promote** new item at 0.65 |
| `user_confirmation` | Boost confidence | Create **pending candidate** at 0.70 |
| `test_failed` | Decay confidence | Create **pending candidate** (diagnostic) at 0.60 |

**Proliferation guard:** Only user corrections and review rejections auto-promote. Confirmations and test failures create extraction candidates that remain pending for human review via `vidya maintain`. This prevents the bag-of-words overlap heuristic (which misses semantic duplicates) from flooding the knowledge base.

**Dual-threshold behavior:** `overlap_score > 0.3` triggers a confidence update (boost/decay). `overlap_score >= 0.5` triggers a merge (evidence combined). Between 0.3 and 0.5, the item is boosted but no merge occurs, and no new item/candidate is created.

**Guarantees:** No learning signal is silently discarded. Feedback is always stored in `feedback_records`. User corrections appear immediately in queries. Confirmations and test failures are captured as candidates for review.

**Error conditions:** Unknown feedback_type triggers no extraction. Missing detail text raises ValueError.

## Key Scenarios

### Source determines initial confidence
**Given** a new knowledge item is created from a user correction
**When** the item is stored with source user_correction
**Then** base_confidence is 0.85, not a flat default

### review_rejected gets lower confidence than user_correction
**Given** no existing items match the feedback text
**When** the feedback engine receives a review_rejected signal
**Then** item is created at 0.65, lower than user_correction (0.85), because agent-initiated rejections carry less epistemic weight

### Time does not reduce confidence
**Given** an item was created 200 days ago and has never been fired
**When** base_confidence is evaluated for ranking
**Then** returns the original source-based confidence unchanged — time passage has no effect

### Unmatched confirmation creates pending candidate
**Given** no existing knowledge items match the confirmation text
**When** the feedback engine receives a user_confirmation signal
**Then** an extraction candidate is created at 0.70 and remains pending for human review — it is NOT auto-promoted

### Unmatched test failure creates pending diagnostic candidate
**Given** no existing knowledge items match the test failure text
**When** the feedback engine receives a test_failed signal
**Then** an extraction candidate of type diagnostic is created at 0.60, pending review

### No signal dropped
**Given** feedback signals of all types arrive
**When** each is processed through the extraction pipeline
**Then** every signal either updates existing items or creates an item/candidate — zero are silently discarded

## Design Decisions

### 1. Remove freshness decay and effective_confidence function

**Decision:** Remove temporal decay entirely. Remove `effective_confidence`, `compute_freshness`, `days_since_reference`. Callers use `base_confidence` directly.

**Rationale:** Procedural knowledge doesn't decay with time. The freshness model created a death spiral — items lost confidence from disuse, fell below query thresholds (0.2), could never fire to rebuild confidence. 70% of items were LOW not because they were wrong but because time passed.

**Why not freshness for tie-breaking?** (Codex review challenge) With ~100 items, ties are rare — FTS relevance and scope boost already differentiate. Any temporal penalty, however small, creates a threshold interaction with `min_confidence` that can make items invisible. Items that become obsolete due to code changes are detected via `superseded_by` and `maintain` review — that's a code event, not a time event.

**Why not a longer half-life (365 days)?** Same category error at slower speed. After 1-2 years, items still decay to the floor. The floor becomes an arbitrary penalty on old-but-correct knowledge.

**Alternatives rejected:**
- Longer half-life: same problem, slower
- Freshness for ranking only: threshold interaction still penalizes
- Freshness for specific types: all current Vidya types are durable (warning/recovery/heuristic obsolescence comes from code changes, detectable via maintain review, not calendar time)
- Keep `effective_confidence` as identity function: YAGNI ceremony

### 2. Source-based initial confidence with 6 tiers

**Decision:** Initial confidence from SOURCE_CONFIDENCE lookup. 6 source tiers with justified spacing.

**Rationale:** Different sources carry different epistemic weight. The old model started ALL feedback-derived items at 0.15 — user corrections (highest value) got lowest confidence. The spacing ensures ranking discrimination: 0.15 between top tiers (correction → confirmation → rejection), 0.05 between lower tiers.

**Why is user_confirmation (0.70) lower than user_correction (0.85)?** Corrections carry specific corrective detail ("use X not Y"). Confirmations may be vague agreement ("yes that's right") without the same precision.

**Why is review_rejected (0.65) separate from user_correction (0.85)?** Codex review identified these have different epistemic weight. An agent-initiated rejection may misinterpret feedback context. A user correction is direct human intent.

**Alternatives rejected:**
- Flat confidence for all: conflates provenance
- All start at 0.5: gives auto-extractions unearned authority
- No review_rejected distinction: conflates agent and human signal

### 3. Two-tier promotion (corrections auto-promote, rest creates candidates)

**Decision:** User corrections and review rejections auto-promote to active knowledge. Confirmations and test failures create pending candidates.

**Rationale:** Codex review identified that auto-promoting every unmatched signal risks item proliferation. The bag-of-words overlap heuristic misses semantic duplicates ("run linting before commit" vs "use a pre-commit linting hook"). User corrections are high-signal and warrant immediate visibility. Confirmations and test failures are lower-signal — the candidate staging table exists precisely for human review.

**Alternatives rejected:**
- Auto-promote all types: proliferation risk without semantic dedup
- Daily cap on auto-created items: arbitrary, loses signal
- Tighter similarity threshold: would also miss legitimate new knowledge

### 4. Migration preserves fire history

**Decision:** Migration recalculates confidence from SOURCE_CONFIDENCE base plus accumulated heuristic updates, rather than blindly resetting.

**Rationale:** Codex review identified that 5 items have fire history (all successes). Resetting to 0.85 discards accumulated evidence. Verified: all 62 negative feedbacks are `user_correction` (zero `review_rejected`), so source attribution to `user_correction` is safe.

**Migration formula:** Start at `SOURCE_CONFIDENCE["user_correction"]` (0.85), then replay `success_count` applications of `base += 0.05 * (1 - base)`. For 1 success: 0.8575. For 2 successes: 0.864625.

### 5. Evidence-based staleness (includes "fired long ago")

**Decision:** Staleness criteria: (a) never fired + older than N days, (b) fired but last_fired older than N days, (c) contradicted (fail_count > success_count), (d) superseded but not archived.

**Rationale:** Codex review identified that removing freshness from staleness would miss items that fired once long ago and became obsolete. Flagging for human review (not auto-penalizing) is the right balance.

## Data Migration

Verified by database query: all 62 negative feedbacks are `user_correction` (zero `review_rejected`). All 57 extraction items trace back to user corrections.

| Source | Count | Current | New | Notes |
|--------|-------|---------|-----|-------|
| extraction (→ user_correction) | 52 | 0.15 | 0.85 | Zero fires, straight remap |
| extraction (→ user_correction) | 3 | 0.1925 | 0.8575 | 1 fire, 1 success: 0.85 + replay |
| extraction (→ user_correction) | 2 | 0.2329 | 0.8646 | 2 fires, 2 successes: 0.85 + replay |
| seed | 22 | 0.60 | 0.60 | Unchanged |

Migration is idempotent — running twice produces the same result. Database backup before migration.

## Files Changed

| File | Changes |
|------|---------|
| `src/vidya/confidence.py` | Remove freshness functions and constants. Remove `effective_confidence`. Add `SOURCE_CONFIDENCE`. Rename "Bayesian" to "heuristic" in docs. |
| `src/vidya/query.py` | Use `base_confidence` directly. Remove freshness imports and computation. |
| `src/vidya/learn.py` | Two-tier promotion. review_rejected distinct. SOURCE_CONFIDENCE for initial values. |
| `src/vidya/store.py` | `promote_candidate` accepts `source` parameter. |
| `src/vidya/maintain.py` | Use `base_confidence` directly. Evidence-based staleness including "fired long ago". |
| `src/vidya/brief.py` | Use `base_confidence` directly. Remove freshness imports. |
| `src/vidya/guidance.py` | Uses `effective_confidence` key from query results — no import changes, but key name stays same in QueryResult. |
| `src/vidya/cli.py` | No import changes — reads `effective_confidence` from QueryResult/dicts. Field renamed internally but key name preserved in output for compatibility. |
| `src/vidya/migrate.py` | New file: one-time migration with history replay. |
| `tests/` | Updated for all above. |

## Dependencies

| Depends On | Why |
|-----------|-----|
| knowledge-storage | CRUD operations for items, candidates, archive |

## Open Questions

None — design validated through conversation and Codex adversarial review.
