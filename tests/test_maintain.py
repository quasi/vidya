"""Tests for maintain.py — stale item detection and health report."""

import pytest
from datetime import datetime, timezone, timedelta

from vidya.store import create_item, update_item, get_item
from vidya.maintain import compute_stats, find_stale_items, health_report, auto_archive_stale


# ---------------------------------------------------------------------------
# compute_stats — uses base_confidence directly (no freshness multiplier)
# ---------------------------------------------------------------------------

def test_compute_stats_confidence_bands_use_base_confidence(db):
    """Stats bands are based on base_confidence, not effective/freshness."""
    create_item(db, pattern="high", guidance="X", item_type="convention",
                base_confidence=0.8, source="seed")
    create_item(db, pattern="medium", guidance="Y", item_type="convention",
                base_confidence=0.3, source="seed")
    create_item(db, pattern="low", guidance="Z", item_type="convention",
                base_confidence=0.1, source="seed")
    stats = compute_stats(db)
    assert stats.by_confidence["high"] == 1
    assert stats.by_confidence["medium"] == 1
    assert stats.by_confidence["low"] == 1


# ---------------------------------------------------------------------------
# find_stale_items — evidence-based staleness
# ---------------------------------------------------------------------------

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
    assert "never fired" in stale[0]["reason"].lower()


def test_find_stale_items_finds_long_unfired_items(db):
    """Items last fired > 90 days ago are stale."""
    item_id = create_item(db, pattern="stale rule", guidance="do Y",
                          item_type="convention", base_confidence=0.4, source="seed")
    old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    update_item(db, item_id, last_fired=old_date, fire_count=5)
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id
    assert "last fired" in stale[0]["reason"].lower()


def test_find_stale_items_finds_contradicted_items(db):
    """Items with fail_count > success_count are stale (contradicted)."""
    item_id = create_item(db, pattern="bad rule", guidance="do Z",
                          item_type="convention", base_confidence=0.4, source="extraction")
    update_item(db, item_id, fail_count=3, success_count=1, fire_count=4)
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert stale[0]["id"] == item_id
    assert "contradicted" in stale[0]["reason"].lower()


def test_find_stale_items_finds_superseded_items(db):
    """Items with superseded_by set and not archived are stale."""
    item_id = create_item(db, pattern="old rule", guidance="do A",
                          item_type="convention", base_confidence=0.6, source="seed")
    other_id = create_item(db, pattern="new rule", guidance="do B",
                           item_type="convention", base_confidence=0.7, source="seed")
    update_item(db, item_id, superseded_by=other_id)
    stale = find_stale_items(db)
    ids = [s["id"] for s in stale]
    assert item_id in ids
    reason = next(s["reason"] for s in stale if s["id"] == item_id)
    assert "superseded" in reason.lower()


def test_find_stale_items_high_confidence_item_not_stale_when_fresh(db):
    """High-confidence, recently-fired item is not stale."""
    item_id = create_item(db, pattern="good rule", guidance="X",
                          item_type="convention", base_confidence=0.8, source="seed")
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    update_item(db, item_id, last_fired=recent, fire_count=10, success_count=8, fail_count=1)
    stale = find_stale_items(db)
    assert stale == []


def test_find_stale_items_output_uses_base_confidence_key(db):
    """Stale item dicts expose base_confidence, not effective_confidence."""
    item_id = create_item(db, pattern="old rule", guidance="do X",
                          item_type="convention", base_confidence=0.3, source="seed")
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    stale = find_stale_items(db)
    assert len(stale) == 1
    assert "base_confidence" in stale[0]
    assert "effective_confidence" not in stale[0]


def test_find_stale_items_respects_scope_filter(db):
    """Language filter narrows stale item search."""
    id1 = create_item(db, pattern="python rule", guidance="X", item_type="convention",
                      language="python", base_confidence=0.3, source="extraction")
    id2 = create_item(db, pattern="go rule", guidance="Y", item_type="convention",
                      language="go", base_confidence=0.3, source="extraction")
    # Make both stale via age
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, id1))
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, id2))
    db.commit()
    stale = find_stale_items(db, language="python")
    ids = [s["id"] for s in stale]
    assert id1 in ids
    assert id2 not in ids


def test_find_stale_items_skips_archived(db):
    """Archived items are not reported as stale."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.3, source="extraction")
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    update_item(db, item_id, status="archived")
    stale = find_stale_items(db)
    assert stale == []


def test_find_stale_items_multiple_reasons(db):
    """An item can accumulate multiple stale reasons."""
    item_id = create_item(db, pattern="bad rule", guidance="X",
                          item_type="convention", base_confidence=0.3, source="extraction")
    old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    update_item(db, item_id, last_fired=old_date, fire_count=4,
                fail_count=3, success_count=1)
    stale = find_stale_items(db)
    assert len(stale) == 1
    # Both "last fired" and "contradicted" in reason
    assert "last fired" in stale[0]["reason"].lower()
    assert "contradicted" in stale[0]["reason"].lower()


# ---------------------------------------------------------------------------
# health_report
# ---------------------------------------------------------------------------

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
    # 3 stale (old, never fired)
    for i in range(3):
        item_id = create_item(db, pattern=f"old {i}", guidance=f"X {i}",
                              item_type="convention", base_confidence=0.5, source="seed")
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    # 1 healthy
    create_item(db, pattern="strong", guidance="Y",
                item_type="convention", base_confidence=0.8, source="seed")
    report = health_report(db)
    assert report["health"] == "degraded"
    assert report["stale_count"] == 3


# ---------------------------------------------------------------------------
# auto_archive_stale
# ---------------------------------------------------------------------------

def test_auto_archive_archives_stale_items(db):
    """Items below archive threshold get archived."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.05, source="extraction")
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    result = auto_archive_stale(db, dry_run=False)
    assert result["archived_count"] == 1
    assert item_id in result["archived_ids"]
    item = get_item(db, item_id)
    assert item["status"] == "archived"


def test_auto_archive_dry_run_does_not_archive(db):
    """Dry run reports but does not archive."""
    item_id = create_item(db, pattern="dead rule", guidance="X",
                          item_type="convention", base_confidence=0.05, source="extraction")
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    db.execute("UPDATE knowledge_items SET first_seen = ? WHERE id = ?", (old_date, item_id))
    db.commit()
    result = auto_archive_stale(db, dry_run=True)
    assert result["archived_count"] == 0
    assert result["would_archive_count"] == 1


def test_auto_archive_skips_items_above_threshold(db):
    """Healthy items (high confidence, recently fired) are not archived."""
    item_id = create_item(db, pattern="good rule", guidance="X",
                          item_type="convention", base_confidence=0.6, source="seed")
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    update_item(db, item_id, last_fired=recent, fire_count=5,
                success_count=4, fail_count=0)
    result = auto_archive_stale(db, dry_run=False)
    assert result["archived_count"] == 0
