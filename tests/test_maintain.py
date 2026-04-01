"""Tests for maintain.py — stale item detection and health report."""

import pytest
from datetime import datetime, timezone, timedelta

from vidya.store import create_item, update_item, get_item
from vidya.maintain import compute_stats, find_stale_items, health_report, auto_archive_stale


def test_find_stale_items_returns_empty_for_fresh_items(db):
    """Items created today are not stale."""
    create_item(db, pattern="test", guidance="test", item_type="convention",
                base_confidence=0.5, source="seed")
    stale = find_stale_items(db)
    assert stale == []


def test_find_stale_items_finds_unfired_old_items(db):
    """Items with first_seen > 90 days ago and never fired are stale."""
    item_id = create_item(db, pattern="old rule", guidance="do X",
                          item_type="convention", base_confidence=0.3, source="seed")
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id
    assert "unfired" in stale[0]["reason"].lower() or "never fired" in stale[0]["reason"].lower()


def test_find_stale_items_finds_long_unfired_items(db):
    """Items last fired > 90 days ago are stale."""
    item_id = create_item(db, pattern="stale rule", guidance="do Y",
                          item_type="convention", base_confidence=0.4, source="seed")
    old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    update_item(db, item_id, last_fired=old_date, fire_count=5)
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id


def test_find_stale_items_finds_low_confidence_items(db):
    """Items with effective confidence below threshold are stale."""
    item_id = create_item(db, pattern="weak rule", guidance="maybe do Z",
                          item_type="convention", base_confidence=0.05, source="extraction")
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id


def test_find_stale_items_respects_scope_filter(db):
    """Language/project filter narrows stale item search."""
    id1 = create_item(db, pattern="python rule", guidance="X", item_type="convention",
                      language="python", base_confidence=0.05, source="extraction")
    id2 = create_item(db, pattern="go rule", guidance="Y", item_type="convention",
                      language="go", base_confidence=0.05, source="extraction")
    stale = find_stale_items(db, language="python")
    ids = [s["id"] for s in stale]
    assert id1 in ids
    assert id2 not in ids


def test_find_stale_items_skips_archived(db):
    """Archived items are not reported as stale."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.05, source="extraction")
    update_item(db, item_id, status="archived")
    stale = find_stale_items(db)
    assert stale == []


def test_health_report_empty_db(db):
    report = health_report(db)
    assert report["total_items"] == 0
    assert report["stale_count"] == 0
    assert report["health"] == "empty"


def test_health_report_healthy_db(db):
    for i in range(5):
        create_item(db, pattern=f"rule {i}", guidance=f"do {i}",
                    item_type="convention", base_confidence=0.6, source="seed")
    report = health_report(db)
    assert report["total_items"] == 5
    assert report["stale_count"] == 0
    assert report["health"] == "healthy"


def test_health_report_degraded_when_many_stale(db):
    """More than 50% stale items → degraded."""
    for i in range(3):
        create_item(db, pattern=f"weak {i}", guidance=f"X {i}",
                    item_type="convention", base_confidence=0.05, source="extraction")
    create_item(db, pattern="strong", guidance="Y",
                item_type="convention", base_confidence=0.8, source="seed")
    report = health_report(db)
    assert report["health"] == "degraded"
    assert report["stale_count"] == 3


def test_auto_archive_archives_stale_items(db):
    """Items below archive threshold get archived."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.05, source="extraction")
    result = auto_archive_stale(db, dry_run=False)
    assert result["archived_count"] == 1
    assert item_id in result["archived_ids"]
    item = get_item(db, item_id)
    assert item["status"] == "archived"


def test_auto_archive_dry_run_does_not_archive(db):
    """Dry run reports but does not archive."""
    create_item(db, pattern="dead rule", guidance="X",
                item_type="convention", base_confidence=0.05, source="extraction")
    result = auto_archive_stale(db, dry_run=True)
    assert result["archived_count"] == 0
    assert result["would_archive_count"] == 1


def test_auto_archive_skips_items_above_threshold(db):
    """Healthy items are not archived."""
    create_item(db, pattern="good rule", guidance="X",
                item_type="convention", base_confidence=0.6, source="seed")
    result = auto_archive_stale(db, dry_run=False)
    assert result["archived_count"] == 0
