# src/vidya/audit.py
"""Knowledge base health audit — read-only diagnostic report."""

import json as _json
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

    return {
        "total_items": len(rows),
        "by_type": by_type,
        "by_scope": by_scope,
        "by_confidence": by_confidence,
    }


def _build_bundles(db: sqlite3.Connection, total_items: int) -> dict[str, Any]:
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
    return {
        "count": count,
        "merge_rate": merge_rate,
        "broken_lineage_count": broken,
        "items_consumed": consumed,
    }


def run_audit(
    db: sqlite3.Connection,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
) -> AuditReport:
    """Read-only diagnostic report on the Vidya knowledge base."""
    where, params = _build_scope_filter(language, runtime, framework, project)
    overview = _build_overview(db, where, params)
    bundles = _build_bundles(db, overview["total_items"])
    return AuditReport(
        overview=overview,
        bundles=bundles,
        clusters_default=[],
        clusters_loose=[],
        candidates={"evolution_pending": 0, "extraction_pending": 0, "oldest_pending_days": None},
        staleness={"untested_count": 0, "contradicted_count": 0, "untested_ids": [], "contradicted_ids": []},
        coverage=[],
        recommendations=[],
    )
