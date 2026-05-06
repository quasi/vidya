---
title: "Design: Knowledge Audit"
type: design
generated_from: canon
feature: knowledge-audit
canon_version: 0.1.0
date: 2026-05-02
tags: [canon, design, knowledge-audit, vidya]
---

# Knowledge Audit — Design Document

## Purpose

Vidya accumulates knowledge over time, but without a way to inspect the health of that accumulation, quality problems go undetected: rules that have never been validated in practice, guidance that has been contradicted more often than confirmed, merge opportunities that could consolidate redundant rules, and hundreds of pending candidates that were never reviewed.

`vidya audit` is a read-only diagnostic command that produces a structured health report across seven dimensions. It tells you what the knowledge base actually looks like — how many rules, how many bundles, where the clusters are, what the backlog looks like — and ends with a ranked list of the specific commands to run next. No writes. No LLM. Pure diagnosis.

## Scope

### In Scope

- Seven-section report: overview, bundle health, cluster analysis (dual threshold), candidate backlog, coverage distribution, staleness signals, recommendations
- Scope filtering: `--language`, `--runtime`, `--framework`, `--project` flags narrow all analysis to the matching item set
- Machine-readable output: top-level `vidya --json audit` flag (shared CLI group pattern) for structured output alongside plain-text default
- Ranked recommendations derived deterministically from report findings

### Out of Scope

- Auto-remediation (`--fix` flag): no writes of any kind
- LLM-assisted analysis: all computation is deterministic
- Archival or promotion operations: those belong in `vidya maintain` and `vidya evolve --review`

## Domain Model

| Entity | Definition | Role in Audit |
|--------|-----------|---------------|
| `KnowledgeItem` | Atomic unit of stored knowledge with pattern, guidance, type, scope, and confidence | Primary unit of analysis |
| `bundle` | A knowledge item of type `bundle` — produced by `evolve` promoting a synthesis candidate | Tracked separately to measure merge activity |
| `related_items` | JSON array on a `bundle` knowledge item recording its source item IDs (set by `promote_candidate`) | Used to assess lineage integrity |
| `bundle_id` | Column on source `knowledge_items` pointing to the bundle that absorbed them (set by `promote_candidate`) | Used to count items consumed by bundles |
| `Cluster` | Ephemeral group of items sharing scope and token overlap, produced by `detect_clusters` | Central to merge-opportunity analysis |
| `evolution_candidate` | A synthesized compound rule awaiting promote/reject decision | Backlog metric |
| `extraction_candidate` | A feedback-derived item awaiting human review before promotion | Backlog metric |
| `fire_count` | How many times an item has been retrieved in a query | Staleness signal: 0 = untested |
| `fail_count` / `success_count` | Outcome counters from `vidya feedback` calls | Staleness signal: fail > success = contradicted |
| `AuditReport` | Dataclass returned by `run_audit()` containing all seven sections | Primary output artifact |

## Report Sections

### 1. Overview
Totals: item count by type (`anti_pattern`, `convention`, `postcondition`, `precondition`, `bundle`), by scope (`global`, `language`, `runtime`, `framework`, `project`), and by confidence band (`HIGH > 0.5`, `MEDIUM 0.2–0.5`, `LOW < 0.2`).

### 2. Bundle Health
- Total bundles and merge rate (bundles / total items × 100)
- `broken_lineage_count`: bundle items where `related_items = '[]'` or `related_items IS NULL` — promoted without recording source IDs. Query: `SELECT id FROM knowledge_items WHERE type='bundle' AND (related_items IS NULL OR related_items='[]')`
- `items_consumed`: count of items that have a non-null `bundle_id` column — tagged as absorbed by a bundle

### 3. Cluster Analysis — Dual Threshold
Two separate cluster lists:
- **Default tier**: `min_size=3, overlap_threshold=0.35, min_cohesion=0.35` — what `vidya evolve` will find on a normal run
- **Loose tier**: `min_size=2, overlap_threshold=0.3, min_cohesion=0.3` — latent merge candidates that evolve won't surface at defaults

Each cluster reports: item count, cohesion score, theme tokens, scope triple. The dual view answers both "what will evolve act on?" and "what else could be merged?"

### 4. Candidate Backlog
- `evolution_pending`: count of `evolution_candidates` with `status = 'pending'`
- `extraction_pending`: count of `extraction_candidates` with `status = 'pending'`
- `oldest_pending_days`: age of the oldest pending candidate across both tables

### 5. Coverage Distribution
Items per project and per scope level. Shows which projects have the most rules and whether global/cross-cutting rules are adequately represented.

### 6. Staleness Signals — Two Tiers
- **Untested** (`fire_count = 0`): items that have never been retrieved — their quality is unknown
- **Contradicted** (`fail_count > success_count`): items whose guidance has been contradicted more often than confirmed — actively harmful

Contradicted items rank above untested items in recommendations because they are known-bad rather than unknown.

### 7. Recommendations
Ranked list of actionable commands derived deterministically from the report data. Priority order:
1. Contradicted items → `vidya explain --item-id <id>` for each
2. Pending evolution candidates → `vidya evolve --review` (requires interactive TTY — run in terminal, not via script)
3. Pending extraction candidates → `vidya items --min-confidence 0` to review; promote via `vidya feedback`
4. Loose-threshold clusters → `vidya evolve --min-size 2 --overlap-threshold 0.3 --min-cohesion 0.3`
5. Broken lineage bundles → informational note (no fix command available)

If the knowledge base is entirely healthy (no contradicted items, empty backlog, no clusters), the recommendations list is empty.

## Behavioral Contract

### `run_audit(db, language=None, runtime=None, framework=None, project=None) -> AuditReport`

**Key Interfaces:**
```
AuditReport:
  overview: dict        — total_items, by_type, by_scope, by_confidence
  bundles: dict         — count, merge_rate, broken_lineage_count, items_consumed
  clusters_default: list[ClusterSummary]
  clusters_loose:   list[ClusterSummary]
  candidates: dict      — evolution_pending, extraction_pending, oldest_pending_days
  staleness: dict       — untested_count, contradicted_count, untested_ids, contradicted_ids
  coverage: list[dict]  — items_per_project, items_per_scope
  recommendations: list[str]

ClusterSummary:
  item_ids: list[str]
  cohesion: float
  theme_tokens: list[str]
  scope: dict
```

**Invariants:**
- `run_audit` never writes to any table — all DB access is read-only. Exception: `init_db()` (used by the standard `_db()` helper) runs idempotent migrations on open; these are acceptable side-effects since migrations are no-ops on an already-initialised DB.
- Scope filters are additive — each non-None filter reduces the item set
- Recommendations are derived deterministically — no LLM calls
- `clusters_loose` may contain different clusters than `clusters_default`, not necessarily a superset — lower thresholds can merge default components into different larger clusters

**Guarantees:**
- Returns a complete `AuditReport` even on empty DB — all numerics default to 0, all lists to `[]`
- `recommendations` is never empty when: `evolution_pending > 0`, `extraction_pending > 0`, `contradicted_count > 0`, or `clusters_loose` is non-empty

**Error behaviour:**
- No exceptions raised for empty knowledge base or empty candidate tables
- All missing-data cases return zero-valued fields

## Key Scenarios

### Full report on a live knowledge base
**Given** 248 active items, 11 bundles, 8 loose clusters, 1 pending evolution candidate, 343 pending extraction candidates  
**When** `vidya audit` is invoked with no scope filters  
**Then** all 7 sections are populated; cluster section shows 0 at defaults and 8 at loose thresholds; recommendations contains at least one entry

### Scoped report
**Given** Items across multiple projects and languages  
**When** `vidya audit --project vidya` is invoked  
**Then** All analysis is restricted to the vidya project scope; totals, clusters, and staleness reflect only the filtered items

### Contradicted items surface first
**Given** Three items with `fail_count > success_count`  
**When** `vidya audit` is invoked  
**Then** `contradicted_count = 3`, item IDs listed, recommendations rank `vidya explain` for each above cluster and backlog entries

## Design Decisions

### Read-only mode
**Problem:** Audit findings imply remediation actions. Should audit act on them?  
**Decision:** Strictly read-only. No `--fix` flag.  
**Rationale:** A diagnostic tool that also mutates state is harder to trust — users cannot know if the report reflects what was there before or after the tool ran. Write operations belong in `evolve --review` and `maintain --archive`, which have explicit confirmation gates.  
**Rejected:** `--fix` flag — cleaner UX but conflates diagnosis with remediation and makes the report non-idempotent.

### Dual-threshold cluster analysis
**Problem:** Default evolve thresholds (`min-size=3, overlap=0.35, cohesion=0.35`) produce zero clusters on the current 248-item base, making the cluster section useless alone.  
**Decision:** Report at BOTH default and loose thresholds (`min-size=2, overlap=0.3, cohesion=0.3`).  
**Rationale:** Defaults show what `evolve` will act on. Loose thresholds show latent opportunities. Both together answer the full question.  
**Rejected:** Fixed loose-only (hides what evolve will do); user-tunable flags (adds decision fatigue, makes reports incomparable).

### Two-tier staleness
**Problem:** `fire_count=0` and `fail_count > success_count` are both quality signals but represent different risks.  
**Decision:** Two distinct tiers — untested and contradicted — with contradicted ranked higher in recommendations.  
**Rationale:** Untested = unknown quality risk. Contradicted = known quality failure. Conflating them loses the severity distinction.  
**Rejected:** Three tiers with confidence degradation — duplicates what `vidya maintain` already surfaces.

### Deterministic recommendations
**Problem:** Recommendations could be LLM-generated (richer) or deterministic (fast, always available).  
**Decision:** Deterministic only. No LLM call.  
**Rationale:** Audit must work without a configured LLM. Deterministic recommendations are fully auditable — the user can trace exactly why each appears.  
**Rejected:** LLM-generated — slow, non-deterministic, requires API key; inappropriate for a CLI diagnostic.

## Dependencies

| Depends On | Why |
|-----------|-----|
| `knowledge-storage` | Reads `knowledge_items`, `evolution_candidates`, `extraction_candidates` tables |
| `knowledge-evolution` | Reuses `detect_clusters()` for both threshold tiers |

## Implementation Notes

- New module: `src/vidya/audit.py` — `run_audit()` function and `AuditReport` dataclass
- New CLI command: `vidya audit` in `src/vidya/cli.py` — thin wrapper with `--language`, `--runtime`, `--framework`, `--project` scope flags. JSON output via the existing top-level `--json` flag on the `cli` group (`ctx.obj.get("json")`), not a per-command flag.
- `detect_clusters()` is called twice per audit run (once per threshold tier). This is O(n²) per scope group — acceptable for knowledge bases under ~1000 items, but implementers should note the scaling characteristic. If performance becomes an issue, the two calls can be merged into a single pass.
- `detect_clusters()` actual parameter name is `overlap_threshold` (not `overlap`) — match exactly.
- No new DB tables or migrations required

## Open Questions

None. All design questions resolved during specification.
