# tests/test_audit.py
"""Tests for audit.py — knowledge base health report."""
import json

import pytest
from vidya.audit import run_audit, AuditReport
from vidya.store import create_item, update_item


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
