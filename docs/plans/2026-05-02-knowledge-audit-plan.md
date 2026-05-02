---
title: "Implementation Plan: Knowledge Audit"
type: implementation-plan
feature: knowledge-audit
design_doc: docs/plans/2026-05-02-knowledge-audit-design.md
date: 2026-05-02
tags: [canon, implementation, knowledge-audit, vidya]
---

# Knowledge Audit — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `vidya audit` — a read-only diagnostic command that reports knowledge base health across 7 sections and outputs ranked actionable recommendations.

**Architecture:** New `audit.py` module with an `AuditReport` dataclass and `run_audit()` function. CLI command in `cli.py` follows the `maintain` command pattern exactly. Reuses `detect_clusters()` from `evolve.py` (called twice, once per threshold tier) and the scope-filter pattern from `maintain.py`.

**Tech stack:** Python 3.11+, Click, SQLite, `dataclasses`, `uv` for test running.

**Baseline:** 208 tests collected, all passing. CCC: 51/70 passing, 0 failures.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/vidya/audit.py` | Create | `AuditReport` dataclass, `run_audit()`, `_build_recommendations()` |
| `src/vidya/cli.py` | Modify | Add `audit` Click command (scope flags, plain-text + JSON output) |
| `tests/test_audit.py` | Create | Tests for `run_audit()` and the `audit` CLI command |

---

## Prerequisites

- All 208 existing tests passing (`VIRTUAL_ENV= uv run pytest tests/ -v`)
- No in-progress `vidya evolve` process running

---

## Dependency Graph

```
Task 1 (AuditReport dataclass + empty-DB skeleton)
  └─► Task 2 (overview + bundle sections)
        └─► Task 3 (cluster sections)
              └─► Task 4 (candidates + staleness)
                    └─► Task 5 (coverage + recommendations)
                          └─► Task 6 (CLI command)
```

**Critical path:** all tasks sequential (each builds on the previous).

---

## Implementation Tasks

### Task 1: `AuditReport` dataclass and `run_audit()` skeleton

**Goal:** Define the output structure and verify empty-DB behaviour.

**Files:**
- Create: `src/vidya/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_audit.py
"""Tests for audit.py — knowledge base health report."""
import pytest
from vidya.audit import run_audit, AuditReport


def test_run_audit_empty_db_returns_zero_report(db):
    """Empty DB returns a fully-structured zero-valued AuditReport."""
    report = run_audit(db)
    assert isinstance(report, AuditReport)
    assert report.overview["total_items"] == 0
    assert report.bundles["count"] == 0
    assert report.clusters_default == []
    assert report.clusters_loose == []
    assert report.candidates["evolution_pending"] == 0
    assert report.candidates["extraction_pending"] == 0
    assert report.staleness["untested_count"] == 0
    assert report.staleness["contradicted_count"] == 0
    assert report.coverage == []
    assert report.recommendations == []
```

- [ ] **Step 1.2: Run test to verify it fails**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py::test_run_audit_empty_db_returns_zero_report -v
Expected: FAIL with "ModuleNotFoundError: No module named 'vidya.audit'"
```

- [ ] **Step 1.3: Write minimal `audit.py`**

```python
# src/vidya/audit.py
"""Knowledge base health audit — read-only diagnostic report."""

import sqlite3
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditReport:
    overview: dict[str, Any] = field(default_factory=dict)
    bundles: dict[str, Any] = field(default_factory=dict)
    clusters_default: list[dict] = field(default_factory=list)
    clusters_loose: list[dict] = field(default_factory=list)
    candidates: dict[str, Any] = field(default_factory=dict)
    staleness: dict[str, Any] = field(default_factory=dict)
    coverage: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def run_audit(
    db: sqlite3.Connection,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
) -> AuditReport:
    """Read-only diagnostic report on the Vidya knowledge base."""
    return AuditReport(
        overview={"total_items": 0, "by_type": {}, "by_scope": {}, "by_confidence": {}},
        bundles={"count": 0, "merge_rate": 0.0, "broken_lineage_count": 0, "items_consumed": 0},
        clusters_default=[],
        clusters_loose=[],
        candidates={"evolution_pending": 0, "extraction_pending": 0, "oldest_pending_days": None},
        staleness={"untested_count": 0, "contradicted_count": 0, "untested_ids": [], "contradicted_ids": []},
        coverage=[],
        recommendations=[],
    )
```

- [ ] **Step 1.4: Run test to verify it passes**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py::test_run_audit_empty_db_returns_zero_report -v
Expected: PASS
```

- [ ] **Step 1.5: Commit**

```bash
git add src/vidya/audit.py tests/test_audit.py
git commit -m "feat(audit): add AuditReport dataclass and run_audit skeleton"
```

---

### Task 2: Overview and bundle sections

**Goal:** Populate `overview` (item totals by type/scope/confidence) and `bundles` (count, merge rate, lineage integrity).

**Files:**
- Modify: `src/vidya/audit.py`
- Modify: `tests/test_audit.py`

- [ ] **Step 2.1: Write failing tests**

```python
# append to tests/test_audit.py
from vidya.store import create_item, update_item


def test_overview_counts_items_by_type(db):
    create_item(db, pattern="p1", guidance="g", item_type="convention",
                base_confidence=0.8, source="seed")
    create_item(db, pattern="p2", guidance="g", item_type="anti_pattern",
                base_confidence=0.3, source="seed")
    report = run_audit(db)
    assert report.overview["total_items"] == 2
    assert report.overview["by_type"]["convention"] == 1
    assert report.overview["by_type"]["anti_pattern"] == 1


def test_overview_counts_confidence_bands(db):
    create_item(db, pattern="high", guidance="g", item_type="convention",
                base_confidence=0.8, source="seed")
    create_item(db, pattern="med", guidance="g", item_type="convention",
                base_confidence=0.35, source="seed")
    create_item(db, pattern="low", guidance="g", item_type="convention",
                base_confidence=0.1, source="seed")
    report = run_audit(db)
    assert report.overview["by_confidence"]["HIGH"] == 1
    assert report.overview["by_confidence"]["MEDIUM"] == 1
    assert report.overview["by_confidence"]["LOW"] == 1


def test_bundle_count_and_merge_rate(db):
    bundle_id = create_item(db, pattern="bundle rule", guidance="g",
                            item_type="bundle", base_confidence=0.7, source="evolution")
    src_id = create_item(db, pattern="source rule", guidance="g",
                         item_type="convention", base_confidence=0.6, source="seed")
    # tag source with bundle lineage
    import json
    update_item(db, bundle_id, related_items=json.dumps([src_id]))
    update_item(db, src_id, bundle_id=bundle_id)
    report = run_audit(db)
    assert report.bundles["count"] == 1
    assert report.bundles["items_consumed"] == 1
    assert report.bundles["broken_lineage_count"] == 0
    assert report.bundles["merge_rate"] > 0


def test_bundle_broken_lineage_detected(db):
    """Bundle with empty related_items is flagged as broken lineage."""
    create_item(db, pattern="bundle rule", guidance="g",
                item_type="bundle", base_confidence=0.7, source="evolution")
    # related_items defaults to '[]' — broken lineage
    report = run_audit(db)
    assert report.bundles["broken_lineage_count"] == 1
```

- [ ] **Step 2.2: Run tests to confirm failure**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "overview or bundle" -v
Expected: FAIL — overview returns zeros, bundle returns zeros
```

- [ ] **Step 2.3: Implement `_build_overview()` and `_build_bundles()`**

```python
# Add to src/vidya/audit.py

def _build_scope_filter(
    language: str | None,
    runtime: str | None,
    framework: str | None,
    project: str | None,
) -> tuple[str, list]:
    """Return (WHERE clause, params) for active items matching scope."""
    conditions = ["status = 'active'"]
    params: list = []
    if language is not None:
        conditions.append("language = ?")
        params.append(language)
    if runtime is not None:
        conditions.append("runtime = ?")
        params.append(runtime)
    if framework is not None:
        conditions.append("framework = ?")
        params.append(framework)
    if project is not None:
        conditions.append("project = ?")
        params.append(project)
    return " AND ".join(conditions), params


def _build_overview(db: sqlite3.Connection, where: str, params: list) -> dict[str, Any]:
    rows = db.execute(
        f"SELECT base_confidence, type, language, runtime, framework, project "
        f"FROM knowledge_items WHERE {where}",
        params,
    ).fetchall()

    by_type: dict[str, int] = {}
    by_scope = {"global": 0, "language": 0, "runtime": 0, "framework": 0, "project": 0}
    by_confidence = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for row in rows:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
        conf = row["base_confidence"]
        if conf > 0.5:
            by_confidence["HIGH"] += 1
        elif conf >= 0.2:
            by_confidence["MEDIUM"] += 1
        else:
            by_confidence["LOW"] += 1
        if row["project"]:
            by_scope["project"] += 1
        elif row["framework"]:
            by_scope["framework"] += 1
        elif row["runtime"]:
            by_scope["runtime"] += 1
        elif row["language"]:
            by_scope["language"] += 1
        else:
            by_scope["global"] += 1

    return {"total_items": len(rows), "by_type": by_type, "by_scope": by_scope, "by_confidence": by_confidence}


def _build_bundles(db: sqlite3.Connection, total_items: int) -> dict[str, Any]:
    import json as _json
    bundles = db.execute(
        "SELECT id, related_items FROM knowledge_items WHERE type = 'bundle' AND status = 'active'"
    ).fetchall()
    count = len(bundles)
    broken = sum(
        1 for b in bundles
        if not b["related_items"] or _json.loads(b["related_items"]) == []
    )
    consumed = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE bundle_id IS NOT NULL AND status = 'active'"
    ).fetchone()[0]
    merge_rate = round(count / total_items * 100, 1) if total_items else 0.0
    return {"count": count, "merge_rate": merge_rate,
            "broken_lineage_count": broken, "items_consumed": consumed}
```

Then wire into `run_audit()`:

```python
def run_audit(db, language=None, runtime=None, framework=None, project=None):
    where, params = _build_scope_filter(language, runtime, framework, project)
    overview = _build_overview(db, where, params)
    bundles = _build_bundles(db, overview["total_items"])
    return AuditReport(
        overview=overview,
        bundles=bundles,
        clusters_default=[], clusters_loose=[],
        candidates={"evolution_pending": 0, "extraction_pending": 0, "oldest_pending_days": None},
        staleness={"untested_count": 0, "contradicted_count": 0, "untested_ids": [], "contradicted_ids": []},
        coverage=[],
        recommendations=[],
    )
```

- [ ] **Step 2.4: Run tests**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "overview or bundle" -v
Expected: ALL PASS
```

- [ ] **Step 2.5: Run full suite to check regressions**

```
Run: VIRTUAL_ENV= uv run pytest tests/ -q
Expected: 208+ tests pass, 0 failures
```

- [ ] **Step 2.6: Commit**

```bash
git add src/vidya/audit.py tests/test_audit.py
git commit -m "feat(audit): implement overview and bundle health sections"
```

---

### Task 3: Cluster analysis (dual threshold)

**Goal:** Populate `clusters_default` and `clusters_loose` by calling `detect_clusters()` at two threshold tiers.

**Files:**
- Modify: `src/vidya/audit.py`
- Modify: `tests/test_audit.py`

- [ ] **Step 3.1: Write failing tests**

```python
# append to tests/test_audit.py

def test_clusters_default_empty_when_no_overlap(db):
    """Items with distinct vocabulary produce no clusters at default thresholds."""
    for i in range(4):
        create_item(db, pattern=f"unique term alpha{i} beta{i} gamma{i}",
                    guidance="do something", item_type="convention",
                    base_confidence=0.7, source="seed")
    report = run_audit(db)
    assert report.clusters_default == []


def test_clusters_loose_finds_similar_items(db):
    """Items sharing significant vocabulary cluster at loose thresholds."""
    for i in range(3):
        create_item(db, pattern="when vidya evolve runs use detect clusters",
                    guidance=f"guidance variant {i}", item_type="convention",
                    base_confidence=0.7, source="seed", project="vidya")
    report = run_audit(db, project="vidya")
    # At loose thresholds (min_size=2) at least one cluster should form
    assert len(report.clusters_loose) >= 1
    cluster = report.clusters_loose[0]
    assert "item_ids" in cluster
    assert "cohesion" in cluster
    assert "theme_tokens" in cluster


def test_cluster_sections_are_dicts_not_dataclasses(db):
    """Cluster summaries are plain dicts (JSON-serialisable)."""
    report = run_audit(db)
    for c in report.clusters_default + report.clusters_loose:
        assert isinstance(c, dict)
```

- [ ] **Step 3.2: Run tests to confirm failure**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "cluster" -v
Expected: FAIL
```

- [ ] **Step 3.3: Implement `_build_clusters()`**

```python
# Add to src/vidya/audit.py
from vidya.evolve import detect_clusters

_DEFAULT_THRESHOLDS = dict(min_size=3, overlap_threshold=0.35, min_cohesion=0.35)
_LOOSE_THRESHOLDS   = dict(min_size=2, overlap_threshold=0.3,  min_cohesion=0.3)


def _cluster_to_dict(c) -> dict:
    return {
        "item_ids": c.item_ids,
        "cohesion": round(c.cohesion, 3),
        "theme_tokens": c.theme_tokens,
        "scope": c.scope,
    }


def _build_clusters(
    db: sqlite3.Connection,
    language: str | None,
    runtime: str | None,
    framework: str | None,
    project: str | None,
) -> tuple[list[dict], list[dict]]:
    # NOTE: detect_clusters does not accept runtime; pass language/framework/project only.
    kw = dict(language=language, framework=framework, project=project)
    default = [_cluster_to_dict(c) for c in detect_clusters(db, **kw, **_DEFAULT_THRESHOLDS)]
    loose   = [_cluster_to_dict(c) for c in detect_clusters(db, **kw, **_LOOSE_THRESHOLDS)]
    return default, loose
```

Wire into `run_audit()`:

```python
clusters_default, clusters_loose = _build_clusters(db, language, runtime, framework, project)
```

- [ ] **Step 3.4: Run tests**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "cluster" -v
Expected: ALL PASS
```

- [ ] **Step 3.5: Full suite**

```
Run: VIRTUAL_ENV= uv run pytest tests/ -q
Expected: all pass
```

- [ ] **Step 3.6: Commit**

```bash
git add src/vidya/audit.py tests/test_audit.py
git commit -m "feat(audit): add dual-threshold cluster analysis sections"
```

---

### Task 4: Candidate backlog and staleness signals

**Goal:** Populate `candidates` (pending evolution + extraction counts) and `staleness` (untested + contradicted items).

**Files:**
- Modify: `src/vidya/audit.py`
- Modify: `tests/test_audit.py`

- [ ] **Step 4.1: Write failing tests**

```python
# append to tests/test_audit.py
import uuid
from datetime import datetime, timezone


def test_candidates_counts_pending_evolution(db):
    """Pending evolution candidates are counted."""
    db.execute(
        "INSERT INTO evolution_candidates "
        "(id, timestamp, pattern, guidance, source_item_ids, cluster_theme, cohesion_score, synthesis_model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(),
         "test pattern", "test guidance", "[]", "theme", 0.5, "test-model"),
    )
    db.commit()
    report = run_audit(db)
    assert report.candidates["evolution_pending"] == 1


def test_candidates_counts_pending_extraction(db):
    """Pending extraction candidates are counted."""
    db.execute(
        "INSERT INTO extraction_candidates "
        "(id, timestamp, pattern, guidance, type, extraction_method, evidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(),
         "test pattern", "test guidance", "convention", "feedback", "[]"),
    )
    db.commit()
    report = run_audit(db)
    assert report.candidates["extraction_pending"] == 1


def test_staleness_untested_items(db):
    """Items with fire_count=0 are flagged as untested."""
    item_id = create_item(db, pattern="untested rule", guidance="g",
                          item_type="convention", base_confidence=0.7, source="seed")
    report = run_audit(db)
    assert report.staleness["untested_count"] == 1
    assert item_id in report.staleness["untested_ids"]


def test_staleness_contradicted_items(db):
    """Items with fail_count > success_count are flagged as contradicted."""
    item_id = create_item(db, pattern="bad rule", guidance="g",
                          item_type="convention", base_confidence=0.5, source="seed")
    update_item(db, item_id, fail_count=3, success_count=1)
    report = run_audit(db)
    assert report.staleness["contradicted_count"] == 1
    assert item_id in report.staleness["contradicted_ids"]


def test_staleness_fired_item_not_untested(db):
    """Item with fire_count > 0 is not in untested."""
    item_id = create_item(db, pattern="fired rule", guidance="g",
                          item_type="convention", base_confidence=0.7, source="seed")
    update_item(db, item_id, fire_count=5)
    report = run_audit(db)
    assert item_id not in report.staleness["untested_ids"]
```

- [ ] **Step 4.2: Run tests to confirm failure**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "candidates or staleness" -v
Expected: FAIL
```

- [ ] **Step 4.3: Implement `_build_candidates()` and `_build_staleness()`**

```python
# Add to src/vidya/audit.py
from datetime import datetime, timezone


def _build_candidates(db: sqlite3.Connection) -> dict[str, Any]:
    evo_pending = db.execute(
        "SELECT COUNT(*), MIN(timestamp) FROM evolution_candidates WHERE status = 'pending'"
    ).fetchone()
    ext_pending = db.execute(
        "SELECT COUNT(*), MIN(timestamp) FROM extraction_candidates WHERE status = 'pending'"
    ).fetchone()

    oldest_ts = None
    for row in [evo_pending, ext_pending]:
        if row[1] and (oldest_ts is None or row[1] < oldest_ts):
            oldest_ts = row[1]

    oldest_days = None
    if oldest_ts:
        ts = datetime.fromisoformat(oldest_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        oldest_days = (datetime.now(timezone.utc) - ts).days

    return {
        "evolution_pending": evo_pending[0],
        "extraction_pending": ext_pending[0],
        "oldest_pending_days": oldest_days,
    }


def _build_staleness(db: sqlite3.Connection, where: str, params: list) -> dict[str, Any]:
    rows = db.execute(
        f"SELECT id, fire_count, fail_count, success_count "
        f"FROM knowledge_items WHERE {where}",
        params,
    ).fetchall()

    untested_ids = [r["id"] for r in rows if r["fire_count"] == 0]
    contradicted_ids = [r["id"] for r in rows if r["fail_count"] > r["success_count"]]
    return {
        "untested_count": len(untested_ids),
        "contradicted_count": len(contradicted_ids),
        "untested_ids": untested_ids,
        "contradicted_ids": contradicted_ids,
    }
```

- [ ] **Step 4.4: Run tests**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "candidates or staleness" -v
Expected: ALL PASS
```

- [ ] **Step 4.5: Full suite**

```
Run: VIRTUAL_ENV= uv run pytest tests/ -q
Expected: all pass
```

- [ ] **Step 4.6: Commit**

```bash
git add src/vidya/audit.py tests/test_audit.py
git commit -m "feat(audit): add candidate backlog and staleness signal sections"
```

---

### Task 5: Coverage distribution and recommendations

**Goal:** Populate `coverage` (items per project/scope) and `recommendations` (deterministic ranked list).

**Files:**
- Modify: `src/vidya/audit.py`
- Modify: `tests/test_audit.py`

- [ ] **Step 5.1: Write failing tests**

```python
# append to tests/test_audit.py

def test_coverage_groups_by_project(db):
    create_item(db, pattern="p1", guidance="g", item_type="convention",
                base_confidence=0.7, source="seed", project="vidya")
    create_item(db, pattern="p2", guidance="g", item_type="convention",
                base_confidence=0.7, source="seed", project="vidya")
    create_item(db, pattern="p3", guidance="g", item_type="convention",
                base_confidence=0.7, source="seed", project="canon")
    report = run_audit(db)
    projects = {c["project"]: c["count"] for c in report.coverage if c.get("project")}
    assert projects.get("vidya") == 2
    assert projects.get("canon") == 1


def test_recommendations_contradicted_items_first(db):
    """Contradicted items produce the highest-priority recommendation."""
    item_id = create_item(db, pattern="bad rule", guidance="g",
                          item_type="convention", base_confidence=0.5, source="seed")
    update_item(db, item_id, fail_count=3, success_count=1)
    report = run_audit(db)
    assert any("explain --item-id" in r for r in report.recommendations)
    # contradicted rec comes before evolution rec
    rec_texts = " | ".join(report.recommendations)
    explain_pos = rec_texts.find("explain --item-id")
    evolve_pos = rec_texts.find("evolve --review")
    if evolve_pos >= 0:
        assert explain_pos < evolve_pos


def test_recommendations_empty_when_healthy(db):
    """No recommendations when knowledge base is completely healthy."""
    item_id = create_item(db, pattern="unique solo term xyz", guidance="g",
                          item_type="convention", base_confidence=0.7, source="seed")
    update_item(db, item_id, fire_count=5, success_count=3, fail_count=0)
    report = run_audit(db)
    assert report.recommendations == []


def test_recommendations_includes_evolve_review_when_pending(db):
    """Pending evolution candidates trigger evolve --review recommendation."""
    db.execute(
        "INSERT INTO evolution_candidates "
        "(id, timestamp, pattern, guidance, source_item_ids, cluster_theme, cohesion_score, synthesis_model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(),
         "compound rule", "guidance text", "[]", "theme", 0.4, "model"),
    )
    db.commit()
    report = run_audit(db)
    assert any("evolve --review" in r for r in report.recommendations)
```

- [ ] **Step 5.2: Run tests to confirm failure**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "coverage or recommendations" -v
Expected: FAIL
```

- [ ] **Step 5.3: Implement `_build_coverage()` and `_build_recommendations()`**

```python
# Add to src/vidya/audit.py

def _build_coverage(db: sqlite3.Connection, where: str, params: list) -> list[dict]:
    rows = db.execute(
        f"SELECT project, COUNT(*) as count FROM knowledge_items WHERE {where} "
        f"GROUP BY project ORDER BY count DESC",
        params,
    ).fetchall()
    return [{"project": r["project"], "count": r["count"]} for r in rows]


def _build_recommendations(report: "AuditReport") -> list[str]:
    recs = []
    # Priority 1: contradicted items (actively harmful)
    for item_id in report.staleness["contradicted_ids"]:
        recs.append(f"vidya explain --item-id {item_id}  # contradicted: fail > success")
    # Priority 2: pending evolution candidates
    if report.candidates["evolution_pending"] > 0:
        n = report.candidates["evolution_pending"]
        recs.append(
            f"vidya evolve --review  # {n} candidate(s) pending — run in interactive terminal"
        )
    # Priority 3: pending extraction candidates
    if report.candidates["extraction_pending"] > 0:
        n = report.candidates["extraction_pending"]
        recs.append(f"vidya items --min-confidence 0  # {n} extraction candidate(s) pending review")
    # Priority 4: loose clusters (merge opportunities)
    if report.clusters_loose:
        n = len(report.clusters_loose)
        recs.append(
            f"vidya evolve --min-size 2 --overlap-threshold 0.3 --min-cohesion 0.3"
            f"  # {n} cluster(s) at loose thresholds"
        )
    # Priority 5: broken bundle lineage (informational)
    if report.bundles["broken_lineage_count"] > 0:
        n = report.bundles["broken_lineage_count"]
        recs.append(
            f"# {n} bundle(s) have no recorded source lineage (related_items empty) — informational only"
        )
    return recs
```

Wire everything into the final `run_audit()`:

```python
def run_audit(db, language=None, runtime=None, framework=None, project=None):
    where, params = _build_scope_filter(language, runtime, framework, project)
    overview = _build_overview(db, where, params)
    bundles = _build_bundles(db, overview["total_items"])
    clusters_default, clusters_loose = _build_clusters(db, language, runtime, framework, project)
    candidates = _build_candidates(db)
    staleness = _build_staleness(db, where, params)
    coverage = _build_coverage(db, where, params)
    report = AuditReport(
        overview=overview,
        bundles=bundles,
        clusters_default=clusters_default,
        clusters_loose=clusters_loose,
        candidates=candidates,
        staleness=staleness,
        coverage=coverage,
        recommendations=[],  # populated after assembly
    )
    report.recommendations = _build_recommendations(report)
    return report
```

- [ ] **Step 5.4: Run tests**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -v
Expected: ALL PASS
```

- [ ] **Step 5.5: Full suite**

```
Run: VIRTUAL_ENV= uv run pytest tests/ -q
Expected: all pass
```

- [ ] **Step 5.6: Commit**

```bash
git add src/vidya/audit.py tests/test_audit.py
git commit -m "feat(audit): add coverage distribution and deterministic recommendations"
```

---

### Task 6: CLI command `vidya audit`

**Goal:** Expose `run_audit()` via the CLI with plain-text and JSON output.

**Files:**
- Modify: `src/vidya/cli.py`
- Modify: `tests/test_audit.py`

- [ ] **Step 6.1: Write failing CLI tests**

```python
# append to tests/test_audit.py
from click.testing import CliRunner
from vidya.cli import main
import json as _json


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """Fixture that points the CLI _db() to a temp database."""
    from vidya.schema import init_db as _init_db
    db_path = str(tmp_path / "test.db")
    conn = _init_db(db_path)
    conn.close()
    monkeypatch.setenv("VIDYA_DB_PATH", db_path)
    return db_path


def test_cli_audit_text_output(cli_db):
    runner = CliRunner()
    result = runner.invoke(main, ["audit"])
    assert result.exit_code == 0
    assert "Overview" in result.output or "Items:" in result.output


def test_cli_audit_json_output(cli_db):
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "audit"])
    assert result.exit_code == 0
    data = _json.loads(result.output)
    assert "overview" in data
    assert "bundles" in data
    assert "candidates" in data
    assert "staleness" in data
    assert "recommendations" in data


def test_cli_audit_project_filter(cli_db):
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "audit", "--project", "vidya"])
    assert result.exit_code == 0
    data = _json.loads(result.output)
    assert data["overview"]["total_items"] == 0  # empty db, filtered
```

- [ ] **Step 6.2: Run tests to confirm failure**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "cli" -v
Expected: FAIL with "No such command 'audit'"
```

- [ ] **Step 6.3: Add `audit` command to `cli.py`**

Add after the `maintain` command (search for `@main.command()` before `def evolve`):

```python
@main.command()
@click.option("--language", default=None, help="Scope filter: language.")
@click.option("--runtime", default=None, help="Scope filter: runtime.")
@click.option("--framework", default=None, help="Scope filter: framework.")
@click.option("--project", default=None, help="Scope filter: project.")
@click.pass_context
def audit(ctx, language, runtime, framework, project):
    """Read-only knowledge base health report."""
    from vidya.audit import run_audit
    db = _db()
    report = run_audit(db, language=language, runtime=runtime,
                       framework=framework, project=project)

    if ctx.obj.get("json"):
        import dataclasses
        click.echo(_json.dumps(dataclasses.asdict(report)))
        return

    ov = report.overview
    click.echo(f"\n=== Knowledge Base Audit ===\n")
    click.echo(f"Overview")
    click.echo(f"  Items:   {ov['total_items']}  "
               f"(HIGH={ov['by_confidence'].get('HIGH',0)} "
               f"MED={ov['by_confidence'].get('MEDIUM',0)} "
               f"LOW={ov['by_confidence'].get('LOW',0)})")
    click.echo(f"  By type: " + "  ".join(f"{k}={v}" for k, v in sorted(ov["by_type"].items())))
    click.echo(f"  By scope: " + "  ".join(f"{k}={v}" for k, v in ov["by_scope"].items() if v > 0))

    b = report.bundles
    click.echo(f"\nBundles")
    click.echo(f"  Count: {b['count']}  Merge rate: {b['merge_rate']}%  "
               f"Broken lineage: {b['broken_lineage_count']}  "
               f"Items consumed: {b['items_consumed']}")

    click.echo(f"\nClusters")
    click.echo(f"  Default thresholds (min_size=3, overlap=0.35, cohesion=0.35): "
               f"{len(report.clusters_default)} cluster(s)")
    click.echo(f"  Loose thresholds   (min_size=2, overlap=0.30, cohesion=0.30): "
               f"{len(report.clusters_loose)} cluster(s)")
    for c in report.clusters_loose[:5]:
        click.echo(f"    cohesion={c['cohesion']:.2f}  items={len(c['item_ids'])}  "
                   f"theme={' '.join(c['theme_tokens'][:6])}")

    cand = report.candidates
    click.echo(f"\nCandidate Backlog")
    click.echo(f"  Evolution pending:  {cand['evolution_pending']}")
    click.echo(f"  Extraction pending: {cand['extraction_pending']}")
    if cand["oldest_pending_days"] is not None:
        click.echo(f"  Oldest pending:    {cand['oldest_pending_days']} days")

    st = report.staleness
    click.echo(f"\nStaleness")
    click.echo(f"  Untested (fire_count=0): {st['untested_count']}")
    click.echo(f"  Contradicted (fail>success): {st['contradicted_count']}")

    if report.coverage:
        click.echo(f"\nCoverage (top projects)")
        for c in report.coverage[:8]:
            label = c["project"] or "(global/unscoped)"
            click.echo(f"  {label}: {c['count']} item(s)")

    if report.recommendations:
        click.echo(f"\nRecommendations")
        for i, rec in enumerate(report.recommendations, 1):
            click.echo(f"  {i}. {rec}")
    else:
        click.echo(f"\nRecommendations: none — knowledge base is healthy")
```

Also add `import json as _json` at the top of `cli.py` if not already present (check first).

- [ ] **Step 6.4: Check `_db()` respects `VIDYA_DB_PATH` env var**

```
Run: grep -n "VIDYA_DB_PATH\|_DB_PATH" src/vidya/cli.py
```

If `_DB_PATH` is hardcoded and doesn't read the env var, update:
```python
import os
_DB_PATH = os.environ.get("VIDYA_DB_PATH", os.path.expanduser("~/.vidya/vidya.db"))
```

- [ ] **Step 6.5: Run CLI tests**

```
Run: VIRTUAL_ENV= uv run pytest tests/test_audit.py -k "cli" -v
Expected: ALL PASS
```

- [ ] **Step 6.6: Run full suite**

```
Run: VIRTUAL_ENV= uv run pytest tests/ -q
Expected: 220+ tests, 0 failures
```

- [ ] **Step 6.7: Smoke test against live DB**

```
Run: uv run vidya audit
Expected: audit report printed with all sections; no exception
```

```
Run: uv run vidya --json audit | python3 -m json.tool | head -20
Expected: valid JSON with overview, bundles, candidates, staleness, recommendations keys
```

- [ ] **Step 6.8: Commit**

```bash
git add src/vidya/cli.py tests/test_audit.py
git commit -m "feat(audit): add vidya audit CLI command with text and JSON output"
```

---

## Estimated Size

| Task | Impl Lines | Test Lines | Total |
|------|-----------|-----------|-------|
| 1. AuditReport + skeleton | ~35 | ~15 | ~50 |
| 2. Overview + bundles | ~50 | ~30 | ~80 |
| 3. Cluster sections | ~25 | ~20 | ~45 |
| 4. Candidates + staleness | ~40 | ~35 | ~75 |
| 5. Coverage + recommendations | ~40 | ~30 | ~70 |
| 6. CLI command | ~55 | ~25 | ~80 |
| **Total** | **~245** | **~155** | **~400** |

---

## Drift Report

*Completed 2026-05-02. Final state: 230 tests passing (208 baseline + 22 new audit tests).*

### What Was Actually Built

`vidya audit` is fully implemented across three files:
- `src/vidya/audit.py` — `AuditReport` dataclass, `run_audit()`, and six private builders (`_build_scope_filter`, `_build_overview`, `_build_bundles`, `_build_clusters`, `_build_candidates`, `_build_staleness`, `_build_coverage`, `_build_recommendations`)
- `src/vidya/cli.py` — `audit` Click command with scope flags and plain-text/JSON output; `VIDYA_DB_PATH` env-var fix applied to `_db()` as a side-effect
- `tests/test_audit.py` — 22 tests covering all 7 report sections, CLI integration (text + JSON + scope filter), and regression guards

All 7 report sections produce correct output. Recommendations are deterministically ranked. Scope filtering is additive. JSON output via `dataclasses.asdict()` is complete.

### Deviations from Plan

#### Deviation: Cluster test data in Task 3
**Planned:** `pattern=f"unique term alpha{i} beta{i} gamma{i}"` for the no-overlap cluster test.
**Actual:** Items with fully disjoint semantic domains (fruit/tools/physics/poetry), each unique in both `pattern` and `guidance`.
**Reason:** `detect_clusters()` tokenises `pattern + guidance` together. The planned test data shared structural tokens (`unique`, `term`) across items, causing false clusters above threshold.
**Spec impact:** None. Better test data. The design doc's no-overlap scenario is still covered.

#### Deviation: `_db()` reads VIDYA_DB_PATH at call time
**Planned:** Not addressed in plan.
**Actual:** `_db()` now reads `os.environ.get("VIDYA_DB_PATH", _DEFAULT_DB_PATH)` at call time, not import time.
**Reason:** Module-level evaluation of `os.environ.get()` occurs before `monkeypatch.setenv()` in tests, making all CLI tests silently hit the real DB. Call-time evaluation is correct for testability.
**Spec impact:** None. Purely an implementation correctness fix.

#### Deviation: Coverage section only groups by project (missing `items_per_scope`)
**Planned:** The plan's `_build_coverage()` code only groups by `project`.
**Actual:** Implementation matches plan — only project grouping.
**Reason:** The plan's own code deviated from the design doc without comment. The design doc states `coverage: list[dict] — items_per_project, items_per_scope`. The scope-level breakdown exists in `overview.by_scope` already.
**Spec impact:** Yes — the design doc's `AuditReport` contract lists `items_per_scope` as a coverage field. Either the coverage section needs a second `GROUP BY` query, or the spec should narrow coverage to project-only and clarify that `overview.by_scope` serves the scope-level view. Decision needed.

#### Deviation: Bundle health and candidate backlog ignore scope filter
**Planned:** The plan's code passed no scope to `_build_bundles()` and `_build_candidates()`.
**Actual:** Implementation matches plan — both query globally.
**Reason:** Bundles span multiple scopes by nature (a bundle synthesises items from different projects); global bundle count is arguably correct regardless of scope. Candidates tables have scope columns but scoping them adds complexity for unclear benefit.
**Spec impact:** Yes — the design doc's "Scoped report" scenario says "All analysis is restricted to the vidya project scope." With `--project=vidya`, the `merge_rate` (global bundle count / scoped item count) becomes meaningless. The spec needs to explicitly state whether bundles and candidates are scope-filtered or always global.

### New Edge Cases Discovered

- **`runtime` leaks into `detect_clusters()`** — `detect_clusters()` has no `runtime` parameter. If `runtime` were naively forwarded from `_build_clusters()`, it would raise `TypeError`. A regression test (`test_audit_with_runtime_filter_does_not_crash`) now guards this.  
  Suggested scenario: *Given* a knowledge base with runtime-scoped items, *When* `run_audit(db, runtime="cpython")` is called, *Then* no exception is raised and the result is a valid `AuditReport`.

- **`detect_clusters()` tokenises pattern + guidance together** — Token overlap is computed across the concatenation of both fields. Test data for the no-overlap case must have disjoint vocabulary in *both* fields, not just the pattern. Future test authors need this constraint.  
  Suggested scenario: *Given* items whose patterns are disjoint but whose guidance shares common words, *When* `run_audit` is called, *Then* cluster detection reflects the combined token space.

- **`oldest_pending_days` is 0 for same-day inserts** — The field computes `(now - oldest_timestamp).days`, which is 0 for any timestamp earlier today. Tests correctly do not assert on this field's value; it is diagnostic output only.

### Decisions Made During Implementation

- **`_db()` call-time env-var reading**: Fixed as a side-effect of Task 6. All CLI tests require it; the original import-time evaluation was a latent bug that would affect any future CLI test using `monkeypatch.setenv`.
- **`broken_lineage` check uses `is None` not falsy**: `b["related_items"] is None` is now used instead of `not b["related_items"]`, matching the spec's `IS NULL OR = '[]'` query exactly.
- **Two-phase `run_audit()` construction**: `AuditReport` is built first, then `report.recommendations = _build_recommendations(report)` is assigned. This is necessary because `_build_recommendations` needs the fully assembled report as input. The dataclass is not frozen, so post-construction assignment is clean.
- **`import dataclasses` moved to module level**: The lazy import inside the `audit` command was inconsistent with cli.py conventions. Moved to module top.

### Suggested Spec Updates

1. **Bundle/candidate scope filter**: Add an explicit invariant to the design doc: "Bundle health and candidate backlog counts are always global (not scope-filtered). The `merge_rate` is computed as global bundles / scoped items only when no scope filter is active." Or alternatively, require scope filtering of both sections and document the implementation complexity.

2. **Coverage section definition**: Narrow `coverage: list[dict] — items_per_project, items_per_scope` to `coverage: list[dict] — project, count` since `overview.by_scope` already provides the scope-level breakdown. Or extend `_build_coverage()` to return two series (one by project, one by scope level).

3. **`detect_clusters()` runtime gap**: Add to implementation notes: "The `runtime` scope dimension is not forwarded to `detect_clusters()` because that function has no `runtime` parameter. Cluster analysis ignores runtime scope even when `--runtime` is provided."

4. **No-overlap cluster test data constraint**: Add to implementation notes or test conventions: "`detect_clusters()` tokenises `pattern + guidance` as a single string. Test data for cluster absence must have disjoint vocabulary in both fields."

### Feeding Back to Canon
`/canon sync --drift-report docs/plans/2026-05-02-knowledge-audit-plan.md`
