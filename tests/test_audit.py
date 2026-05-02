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


def test_clusters_default_empty_when_no_overlap(db):
    """Items with fully distinct vocabulary produce no clusters at default thresholds."""
    # Each item has a completely disjoint token set — no shared words whatsoever.
    items = [
        ("apple mango papaya guava",        "citrus ripe tropical harvest"),
        ("wrench hammer chisel lathe",       "torque drill fasten bolt"),
        ("neutron proton electron quark",    "orbit decay fission nucleus"),
        ("sonnet haiku limerick stanza",     "rhyme meter verse couplet"),
    ]
    for pattern, guidance in items:
        create_item(db, pattern=pattern, guidance=guidance, item_type="convention",
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
