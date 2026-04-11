---
title: "Design: Knowledge Evolution"
type: design
generated_from: canon
feature: knowledge-evolution
canon_version: 0.1.0
date: 2026-04-10
tags: [canon, design, knowledge-evolution, evolve, clustering, synthesis]
---

# Knowledge Evolution — Design Document

## Purpose

Vidya accumulates individual knowledge items from user corrections, confirmations, and seeded rules. Over time, related items pile up without compounding — five separate rules about CCC scenario formatting remain five separate query results instead of one coherent compound rule. Knowledge Evolution closes this gap: it clusters related items, synthesizes compound rules via LLM, and promotes them through human-gated review.

The feature addresses the architectural gap identified in both the Vidya council synthesis ("no mechanism to synthesize patterns across items") and the ECC research distillation ("the `/evolve` pattern — clustering observations into higher-order skills").

**Who benefits**: Baba, as the knowledge base becomes more concise and coherent. Agent consumers, who receive fewer but more informative rules per query.

## Scope

### In Scope

- Clustering active knowledge items by scope and FTS5 token overlap
- LLM-based synthesis of compound rules from clusters
- Human review flow (approve, edit, reject, skip)
- Promotion of approved compounds to `bundle` items
- Display-layer compaction (sources remain queryable, grouped at presentation)
- Decomposition of bundles on negative feedback with human-routed corrections

### Out of Scope

- Embedding-based semantic clustering (Phase 2 if FTS5 proves insufficient)
- Automatic promotion without human review
- Cross-scope clustering (items in different scope triples never cluster)
- Frequency-extraction or contrast-extraction (separate features, different abstraction level)

## Domain Model

| Entity | Definition | Relationships |
|--------|-----------|---------------|
| Cluster | Ephemeral group of related items sharing scope and token overlap ≥ threshold | Contains 3+ knowledge items; produces one EvolutionCandidate |
| EvolutionCandidate | Proposed compound rule synthesized by LLM from a cluster | Stored in `extraction_candidates` with `extraction_method = 'evolution'`; references source item IDs |
| Bundle | A promoted compound rule that replaces its source items in queries | Knowledge item with `type = 'bundle'`; `related_items` points to source IDs |
| Cohesion Score | Average pairwise token overlap within a cluster (0.0–1.0) | Property of Cluster; higher = tighter thematic grouping |

## Behavioral Contracts

### Cluster Detection

Identifies groups of related items within the same scope triple (language, framework, project). Uses FTS5 token overlap as the similarity metric: for each pair of items, compute `shared_tokens / min(tokens_a, tokens_b)`. Items with overlap ≥ 0.4 (configurable threshold) form edges in an adjacency graph. Connected components of size ≥ 3 (configurable minimum) become clusters.

**Key constraints:**
- Items with `status = 'archived'` are excluded
- An item appears in at most one cluster per evolve run
- Scope boundary is hard — items in different scope triples never cluster, regardless of token overlap
- **Cohesion gate:** After extracting connected components, compute average pairwise overlap. Reject components below 0.5 cohesion — prevents hub items with generic tokens from bridging unrelated subtopics
- Cluster theme is derived from centroid tokens (tokens appearing in >50% of members)

### Compound Synthesis

Transforms a cluster into a single compound rule via LLM. The LLM receives all member items' pattern + guidance verbatim and produces a consolidated pattern (≤ 15 words) and guidance paragraph (imperative voice, preserving every concrete detail).

**LLM configuration:**
- Default model: `claude-haiku-4-5` (small input, small output — Haiku is cost-appropriate)
- Configurable via `VIDYA_EVOLVE_MODEL` environment variable
- Failure mode: LLM unavailable → skip cluster, log warning, continue

**Quality signals:**
- If synthesized guidance is shorter than the shortest source item → flag in `review_notes`
- If synthesis drops a source item's key details → caught by human review

### Evolution Lifecycle

The promotion/decomposition lifecycle:

```
vidya evolve
    │
    ├── --cluster-only    → show clusters, no synthesis
    ├── --dry-run         → synthesize but don't persist
    └── --review          → present pending candidates for review
          │
          ├── approve     → create bundle, tag sources with bundle_id
          ├── edit        → modify guidance, then promote
          ├── reject      → discard candidate permanently
          └── skip        → leave pending for next review
```

**Retrieval vs Display (revised after design critique):** Source items remain `status = 'active'` and fully queryable via FTS. Bundling adds a `bundle_id` field to source items pointing to the bundle. At presentation time, the query engine groups results sharing a `bundle_id` and shows the bundle's synthesized guidance instead of individual source texts. The FTS retrieval surface is preserved; only the display is compacted.

**Decomposition trigger:** When a bundle item receives `user_correction` or `test_failed` feedback:
1. Bundle → `status = 'superseded'`
2. All source items → `bundle_id` cleared (resume individual display)
3. Source items are presented to the user with the correction text — human explicitly routes the correction to the relevant source(s). No automatic blame assignment.

**Query behavior:** Source items match FTS queries normally. When results share a `bundle_id`, they are grouped at presentation and the bundle guidance is shown once. Net effect: retrieval recalls everything, display is compacted.

## Key Design Decisions

### D1: FTS5 Token Overlap for Clustering (not embeddings)

**Problem:** How to detect which items are related enough to cluster.

**Chosen:** FTS5 token overlap ratio within same scope triple.

**Why:** Most Vidya items are about specific CLI flags, functions, error messages — they share literal tokens when related. FTS5 is zero-dependency (already indexed), fast, and deterministic. Embeddings would require sqlite-vec or an external service, adding complexity for marginal improvement on syntactically-similar items.

**Rejected:** Embedding-based (dependency overhead), manual tagging (defeats automation), LLM classification per-pair (O(n²) LLM calls).

**Risk:** Semantically related items that don't share tokens (e.g., "SQLite CREATE TABLE doesn't migrate" and "nodes.put() with sparse dict erases data") won't cluster. Acceptable for V1.

### D2: LLM Synthesis with Human Review Gate (not concatenation)

**Problem:** How to produce a readable compound rule from 3-5 source items.

**Chosen:** LLM synthesis producing a coherent paragraph, reviewed by human before promotion.

**Why:** Concatenation produces walls of text. LLM produces readable, actionable rules. Brooks's concern about LLM quality (from the Skills & Expertise council) is addressed by the human gate — bad synthesis gets rejected.

**Rejected:** Concatenation (noisy), template-based (too rigid), tag-grouping without synthesis (loses compounding).

### D3: Manual Invocation (not automatic)

**Problem:** When should the evolve loop run.

**Chosen:** Manual `vidya evolve` command.

**Why:** Evolve produces candidates requiring human review. Automatic invocation would accumulate unreviewed candidates. Manual means Baba runs it when ready to review — same pattern as `vidya maintain`.

**Rejected:** Automatic on task end (review backlog), periodic (requires session counting), piggybacked on maintain (conflates pruning with synthesis).

## Schema Changes

### knowledge_items changes

- New `type` value: `bundle` — identifies compound rules
- New column: `bundle_id TEXT` — on source items, points to the bundle item they belong to. NULL when not bundled.
- `related_items` field (existing JSON column) — on bundle items, stores source item IDs

### New table: evolution_candidates

Dedicated lifecycle table, separate from `extraction_candidates` (which handles feedback-derived candidates with different approval semantics).

```sql
CREATE TABLE IF NOT EXISTS evolution_candidates (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    pattern TEXT NOT NULL,
    guidance TEXT NOT NULL,
    source_item_ids TEXT NOT NULL,  -- JSON array of knowledge_item IDs
    scope_language TEXT,
    scope_framework TEXT,
    scope_project TEXT,
    cluster_theme TEXT NOT NULL,
    cohesion_score REAL NOT NULL,
    synthesis_model TEXT NOT NULL,
    status TEXT DEFAULT 'pending',  -- pending, promoted, rejected
    review_notes TEXT
);
```

### Migration

One `ALTER TABLE` for `bundle_id` column, one `CREATE TABLE` for `evolution_candidates`. Both are additive — no data migration needed.

## Design Critique Response

A design review identified 4 findings (1 CRITICAL, 2 HIGH, 1 MODERATE). All were accepted and incorporated:

| # | Finding | Resolution |
|---|---------|------------|
| 1 (CRITICAL) | Bundling removes FTS retrieval surface | **Revised**: sources stay `active` and queryable. Bundle is display-layer compaction only via `bundle_id` grouping. |
| 2 (HIGH) | Mixed candidate queue with incompatible semantics | **Revised**: dedicated `evolution_candidates` table with separate review command and promotion codepath. |
| 3 (HIGH) | Heuristic source assignment on decomposition | **Revised**: no automatic correction routing. Human explicitly chooses which source(s) the correction applies to. |
| 4 (MODERATE) | Connected-component bridging via hub items | **Revised**: cohesion gate (min 0.5 average pairwise overlap) rejects low-cohesion components before synthesis. |

## Open Questions

1. **Should bundle confidence track independently?** Sources remain active with their own confidence. The bundle has its own `base_confidence` (initially averaged from sources). Should positive feedback on the bundle also boost source items, or only the bundle? Current design: bundle-only — sources maintain independent confidence.

2. **Re-clustering after decomposition:** If a bundle of 5 decomposes and 4 are still valid, the next `vidya evolve` will naturally re-cluster them if they still meet the threshold. The corrected 5th item may or may not rejoin depending on how much the correction changed its tokens.

3. **Threshold tuning:** Both the 0.4 token overlap threshold and 0.5 cohesion threshold are starting values. Need empirical validation after 2-3 evolve runs on real data.

4. **Bundle display in hooks:** The UserPromptSubmit hook injects Vidya knowledge. How should it display bundled results? Options: show bundle guidance with "(compacted from N items)" note, or show individual items as today and let the user benefit from compaction only in `vidya query` output.
