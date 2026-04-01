"""Tests for maintain.py — stale item detection and health report."""

import pytest
from datetime import datetime, timezone, timedelta

from vidya.store import create_item, update_item
from vidya.maintain import compute_stats, find_stale_items


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
