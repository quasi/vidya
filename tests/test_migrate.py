import pytest
from vidya.confidence import SOURCE_CONFIDENCE, TRUST_GROWTH
from vidya.migrate import migrate_confidence_model
from vidya.store import create_item


def test_migration_updates_extraction_to_user_correction(db):
    """Items from extraction get source=user_correction and confidence 0.85."""
    item_id = create_item(db, pattern="test", guidance="test",
                          item_type="convention", source="extraction",
                          base_confidence=0.15)
    result = migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    assert item["source"] == "user_correction"
    assert item["base_confidence"] == pytest.approx(0.85)
    assert result["updated_count"] == 1


def test_migration_replays_fire_history(db):
    """Items with successful fires get confidence above 0.85."""
    item_id = create_item(db, pattern="test replay1", guidance="test replay1",
                          item_type="convention", source="extraction",
                          base_confidence=0.1925)
    db.execute("UPDATE knowledge_items SET fire_count=1, success_count=1 WHERE id=?", (item_id,))
    db.commit()
    migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    expected = 0.85 + TRUST_GROWTH * (1.0 - 0.85)
    assert item["base_confidence"] == pytest.approx(expected)
    assert item["source"] == "user_correction"


def test_migration_replays_two_successes(db):
    """Items with 2 fires get two rounds of heuristic growth."""
    item_id = create_item(db, pattern="test replay2", guidance="test replay2",
                          item_type="convention", source="extraction",
                          base_confidence=0.232875)
    db.execute("UPDATE knowledge_items SET fire_count=2, success_count=2 WHERE id=?", (item_id,))
    db.commit()
    migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    base = 0.85
    base += TRUST_GROWTH * (1.0 - base)
    base += TRUST_GROWTH * (1.0 - base)
    assert item["base_confidence"] == pytest.approx(base)


def test_migration_leaves_seed_items_unchanged(db):
    """Seed items keep their source and confidence."""
    item_id = create_item(db, pattern="test seed", guidance="test seed",
                          item_type="convention", source="seed",
                          base_confidence=0.60)
    migrate_confidence_model(db)
    item = db.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
    assert item["source"] == "seed"
    assert item["base_confidence"] == pytest.approx(0.60)


def test_migration_is_idempotent(db):
    """Running migration twice produces the same result."""
    create_item(db, pattern="test idemp", guidance="test idemp",
                item_type="convention", source="extraction",
                base_confidence=0.15)
    r1 = migrate_confidence_model(db)
    r2 = migrate_confidence_model(db)
    assert r1["updated_count"] == 1
    assert r2["updated_count"] == 0
