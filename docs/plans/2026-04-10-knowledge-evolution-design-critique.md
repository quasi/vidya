## Context

The design proposes a new `vidya evolve` flow that clusters active knowledge items by token overlap, synthesizes one compound rule per cluster via LLM, stores that proposal in `extraction_candidates`, and on approval promotes it to a `bundle` item while marking the source items `bundled` so they disappear from normal queries.

The current system matters here:
- Query retrieval is FTS-first over each item's own `pattern`/`guidance`, and only `status = 'active'` items participate ([`src/vidya/query.py:53`](../src/vidya/query.py), [`src/vidya/query.py:186`](../src/vidya/query.py)).
- Feedback learning also only matches against `status = 'active'` items, so bundled source items would be invisible to the existing correction/update path ([`src/vidya/learn.py:101`](../src/vidya/learn.py), [`src/vidya/learn.py:117`](../src/vidya/learn.py)).
- `extraction_candidates` is already the shared staging area for unmatched feedback candidates, with a single pending-review lifecycle and a generic `promote_candidate()` path ([`src/vidya/store.py:292`](../src/vidya/store.py), [`src/vidya/store.py:322`](../src/vidya/store.py)).

Assumptions inferred from the design:
- The primary problem is result-set compaction, not raw knowledge loss.
- Query quality must remain at least as good after compaction.
- Human review is intended to catch synthesis mistakes, but not to compensate for structural retrieval defects.

## Findings

### [CRITICAL] Bundling removes the lexical retrieval surface that the current query engine depends on

**Where:** Query replacement model in the design's lifecycle and query behavior ([`docs/plans/2026-04-10-knowledge-evolution-design.md:90`](./2026-04-10-knowledge-evolution-design.md#L90), [`docs/plans/2026-04-10-knowledge-evolution-design.md:95`](./2026-04-10-knowledge-evolution-design.md#L95)); current FTS ranking path ([`src/vidya/query.py:87`](../src/vidya/query.py), [`src/vidya/query.py:103`](../src/vidya/query.py)).

**What:** The design replaces several source items with a single synthesized bundle even though retrieval today is driven by literal FTS matches on each item's own text.

**Why it matters:** Compression and retrieval are being treated as the same operation. They are not. The current engine matches many narrow lexical surfaces; the proposed bundle collapses them into one short pattern and one paragraph. That means contexts that matched one specific source item may stop matching anything once those sources become `bundled`. In practice, the feature can improve display concision while degrading recall, which is the opposite of the system's core job. Human review cannot detect this reliably because the failure only appears later under diverse query phrasings.

**Recommendation:** Decouple retrieval compaction from source suppression. Keep source items queryable for match generation, then collapse them at presentation time into a bundle-backed result group, or index bundles with the full source text/evidence so they preserve the original lexical surface. Do not make source disappearance the primary mechanism.

**Trade-off:** Query and explain paths get more complex because they must support grouped results or expanded indexing, but that complexity is cheaper than shipping a compaction feature that silently makes knowledge harder to retrieve.

### [HIGH] Reusing `extraction_candidates` creates a mixed review queue with incompatible semantics

**Where:** Evolution candidates are stored in `extraction_candidates` ([`docs/plans/2026-04-10-knowledge-evolution-design.md:44`](./2026-04-10-knowledge-evolution-design.md#L44), [`docs/plans/2026-04-10-knowledge-evolution-design.md:143`](./2026-04-10-knowledge-evolution-design.md#L143)); that same table already holds unmatched feedback candidates ([`src/vidya/learn.py:240`](../src/vidya/learn.py), [`src/vidya/store.py:292`](../src/vidya/store.py)).

**What:** The design puts two materially different things into one pending-review queue: feedback-derived candidate items and evolution-derived bundle proposals.

**Why it matters:** These candidates do not mean the same thing and should not be reviewed the same way. A feedback candidate is "possibly new knowledge"; an evolution candidate is "replace these existing active items with a synthesized abstraction." Approval of the latter has destructive consequences on existing query behavior, while approval of the former only adds knowledge. Mixing them behind one status model and one generic promotion path creates hidden coupling in CLI review, auditing, explainability, and future automation. The reviewer must now infer semantics from `extraction_method` everywhere, or the wrong approval path will promote the wrong object incorrectly.

**Recommendation:** Give evolution proposals their own lifecycle boundary: either a dedicated `evolution_candidates` table, or at minimum a separate review command and promotion codepath with explicit invariants for bundle creation, source-state mutation, and rejection handling. Shared storage is acceptable only if lifecycle operations are also explicitly separated.

**Trade-off:** This adds schema or codepath surface area, but it restores clear ownership and prevents the review queue from becoming an overloaded catch-all.

### [HIGH] Decomposition assigns bundle corrections to a single source item using a heuristic that does not match the abstraction being created

**Where:** Decomposition rule step 3 ([`docs/plans/2026-04-10-knowledge-evolution-design.md:90`](./2026-04-10-knowledge-evolution-design.md#L90)); current feedback matcher only operates over active items and uses shallow overlap ([`src/vidya/learn.py:101`](../src/vidya/learn.py), [`src/vidya/learn.py:130`](../src/vidya/learn.py)).

**What:** When feedback hits a bundle, the design decomposes the bundle and then applies the correction to whichever source item has the highest FTS overlap to the correction text.

**Why it matters:** A correction against a synthesized rule is often about the interaction or boundary between multiple source items, not one hidden "true" source. Mapping that feedback onto a single source item by token overlap is structurally unsound: it can rewrite the wrong source, preserve the wrong four items, and leave the actual defect unaddressed. Because bundled sources were previously excluded from normal feedback matching, this heuristic becomes the only recovery path right at the moment the system most needs precision.

**Recommendation:** Treat bundle-level negative feedback as feedback on the bundle artifact first, not as an immediate source-item correction. Decompose the bundle, mark the source set for explicit review, and require human choice or stronger provenance to attach corrective feedback to individual sources. If automatic routing is kept, it needs a confidence threshold and a "no safe target" branch.

**Trade-off:** Recovery becomes slower and less automatic, but it avoids poisoning source knowledge with speculative blame assignment.

### [MODERATE] Connected-component clustering will bridge loosely related rules into one candidate

**Where:** Cluster detection algorithm ([`docs/plans/2026-04-10-knowledge-evolution-design.md:52`](./2026-04-10-knowledge-evolution-design.md#L52)).

**What:** The design defines a cluster as any connected component in the overlap graph, even if some members only connect transitively through a hub item.

**Why it matters:** Connected components are good at finding "anything vaguely connected," not "one coherent theme." A single generic rule can bridge several adjacent subtopics and drag them into one synthesis candidate. The cohesion score is recorded after the fact, but nothing in the contract prevents low-cohesion transitive clusters from reaching the LLM. That creates avoidable review noise and increases the chance of compound rules that read well but collapse distinct constraints into one abstraction.

**Recommendation:** Add a pre-synthesis coherence gate. Examples: require minimum average pairwise overlap, reject clusters with high variance, or cluster with a stronger method than plain connected components. At minimum, make low cohesion block synthesis rather than merely annotate it.

**Trade-off:** Fewer clusters will be eligible in V1, but the ones that remain will more reliably represent a real compound rule rather than a graph artifact.

## Risk Summary

| # | Severity | Finding | Failure Mode |
|---|----------|---------|--------------|
| 1 | CRITICAL | Bundling removes lexical retrieval surface | Fewer items are returned because the surviving bundle no longer matches the specific contexts that the original items matched |
| 2 | HIGH | Mixed candidate queue with incompatible semantics | Review/promotion logic conflates "add new item" and "replace active items," causing incorrect approval flows and weak auditability |
| 3 | HIGH | Heuristic source assignment on decomposition | Negative feedback against a bundle gets attached to the wrong source item, corrupting the underlying knowledge base |
| 4 | MODERATE | Connected-component bridging | Transitive token overlap creates incoherent synthesis candidates that look thematically related but are not one real rule |

## Assessment

The design is aimed at a real problem, but the current proposal couples compaction, retrieval, and recovery too tightly. The primary issue is structural: in a system whose query engine is FTS-driven per item, replacing source items with a shorter synthesized artifact is likely to reduce recall. Address that first, then separate the evolution lifecycle from the existing generic candidate pipeline before implementation.
