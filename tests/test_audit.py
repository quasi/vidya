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
