"""Tests for knowledge evolution schema — Task 1: Schema Changes."""

import sqlite3
import tempfile
import os

import pytest

from vidya.schema import init_db
from vidya.store import create_item


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_schema_evolution_candidates_created(db):
    """evolution_candidates table exists after init_db."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_candidates'"
    ).fetchone()
    assert row is not None, "evolution_candidates table not found"


def test_schema_evolution_candidates_columns(db):
    """evolution_candidates has all required columns."""
    info = db.execute("PRAGMA table_info(evolution_candidates)").fetchall()
    columns = {row["name"] for row in info}
    required = {
        "id", "timestamp", "pattern", "guidance", "source_item_ids",
        "scope_language", "scope_framework", "scope_project",
        "cluster_theme", "cohesion_score", "synthesis_model",
        "status", "review_notes",
    }
    missing = required - columns
    assert not missing, f"Missing columns in evolution_candidates: {missing}"


def test_schema_bundle_id_column_exists(db):
    """knowledge_items has a bundle_id column."""
    info = db.execute("PRAGMA table_info(knowledge_items)").fetchall()
    columns = {row["name"] for row in info}
    assert "bundle_id" in columns, "bundle_id column missing from knowledge_items"


def test_schema_bundle_id_index_exists(db):
    """idx_bundle_id index exists on knowledge_items."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_bundle_id'"
    ).fetchone()
    assert row is not None, "idx_bundle_id index not found"


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

def test_migration_idempotent():
    """migrate_add_evolution can be called twice without error."""
    from vidya.schema import migrate_add_evolution

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        # Create base schema without bundle_id / evolution_candidates
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_items (
                id TEXT PRIMARY KEY,
                language TEXT,
                runtime TEXT,
                framework TEXT,
                project TEXT,
                pattern TEXT NOT NULL,
                guidance TEXT NOT NULL,
                type TEXT NOT NULL,
                details_json TEXT,
                tags TEXT DEFAULT '[]',
                base_confidence REAL DEFAULT 0.0,
                source TEXT DEFAULT 'observation',
                evidence TEXT DEFAULT '[]',
                counter_evidence TEXT DEFAULT '[]',
                first_seen TEXT NOT NULL,
                last_fired TEXT,
                fire_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                overrides TEXT,
                superseded_by TEXT,
                related_items TEXT DEFAULT '[]',
                version INTEGER DEFAULT 1,
                explanation TEXT,
                status TEXT DEFAULT 'active'
            );
        """)
        conn.commit()

        # First call — should add column and create table + index
        migrate_add_evolution(conn)

        # Second call — must not raise
        migrate_add_evolution(conn)

        # Verify column exists
        info = conn.execute("PRAGMA table_info(knowledge_items)").fetchall()
        columns = {row["name"] for row in info}
        assert "bundle_id" in columns

        # Verify table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_candidates'"
        ).fetchone()
        assert row is not None

        conn.close()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

def test_bundle_type_valid(db):
    """'bundle' is accepted as a valid item type by create_item."""
    item_id = create_item(
        db,
        pattern="Group: prefer async patterns",
        guidance="All items in this cluster share an async theme.",
        item_type="bundle",
    )
    assert item_id is not None
    item = db.execute(
        "SELECT type FROM knowledge_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert item["type"] == "bundle"


def test_bundle_id_writable(db):
    """bundle_id can be set on a knowledge_item via update_item."""
    from vidya.store import update_item

    item_id = create_item(
        db,
        pattern="Use context managers for resources",
        guidance="Always use with-blocks for files and connections.",
        item_type="convention",
    )
    bundle_id = "bundle-abc-123"
    update_item(db, item_id, bundle_id=bundle_id)

    row = db.execute(
        "SELECT bundle_id FROM knowledge_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert row["bundle_id"] == bundle_id
