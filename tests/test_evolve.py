"""Tests for knowledge evolution schema — Task 1: Schema Changes."""

import json
import sqlite3
import tempfile
import os

import pytest

from vidya.schema import init_db
from vidya.store import create_item, update_item


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


# ---------------------------------------------------------------------------
# Cluster detection tests (Task 2)
# ---------------------------------------------------------------------------

from vidya.evolve import detect_clusters, Cluster  # noqa: E402


def test_cluster_same_scope(db):
    """Items sharing significant token overlap in same scope form a cluster."""
    # Items about "error handling" in Python — lots of shared vocabulary
    for i in range(3):
        create_item(
            db,
            pattern=f"error handling python exception catch {i}",
            guidance="always handle errors with try except blocks catch exception python",
            item_type="convention",
            language="python",
            framework=None,
            project="myapp",
        )

    clusters = detect_clusters(db, language="python", project="myapp", min_size=3)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert len(cluster.item_ids) == 3
    assert cluster.scope == {"language": "python", "framework": None, "project": "myapp"}
    assert cluster.cohesion > 0.0


def test_cluster_scope_isolation(db):
    """Items in different scope triples never cluster together."""
    # 3 items in project "alpha"
    for i in range(3):
        create_item(
            db,
            pattern=f"error handling python exception catch {i}",
            guidance="always handle errors with try except blocks catch exception python",
            item_type="convention",
            language="python",
            project="alpha",
        )
    # 3 items in project "beta" — same text, different project
    for i in range(3):
        create_item(
            db,
            pattern=f"error handling python exception catch {i}",
            guidance="always handle errors with try except blocks catch exception python",
            item_type="convention",
            language="python",
            project="beta",
        )

    # With no project filter, two separate clusters should be found
    clusters = detect_clusters(db, language="python", min_size=3)
    assert len(clusters) == 2
    projects = {c.scope["project"] for c in clusters}
    assert projects == {"alpha", "beta"}

    # Each cluster has only items from its own project
    for cluster in clusters:
        assert len(cluster.item_ids) == 3


def test_cluster_excludes_archived(db):
    """Archived items are not included in cluster detection."""
    item_ids = []
    for i in range(4):
        iid = create_item(
            db,
            pattern=f"error handling python exception catch {i}",
            guidance="always handle errors with try except blocks catch exception python",
            item_type="convention",
            language="python",
            project="myapp",
        )
        item_ids.append(iid)

    # Archive two of the four items — drops cluster below min_size=3
    update_item(db, item_ids[0], status="archived")
    update_item(db, item_ids[1], status="archived")

    clusters = detect_clusters(db, language="python", project="myapp", min_size=3)
    assert clusters == []


def test_cluster_cohesion_gate(db):
    """Components whose average pairwise overlap is below min_cohesion are rejected."""
    # Items with very different vocabularies that happen to share just enough tokens
    # to form a connected component but not enough for cohesion >= 0.5
    texts = [
        ("logging setup config file rotation", "configure logging with rotating file handler"),
        ("async event loop coroutine await", "use asyncio event loop and await coroutines"),
        ("database connection pool transaction", "manage database connections with pool and transactions"),
    ]
    for pattern, guidance in texts:
        create_item(
            db,
            pattern=pattern,
            guidance=guidance,
            item_type="convention",
            language="python",
            project="myapp",
        )

    # With a very low overlap_threshold items might connect but cohesion should be low.
    # Use overlap_threshold=0.1 to force connectivity, then cohesion gate should reject.
    clusters = detect_clusters(
        db,
        language="python",
        project="myapp",
        min_size=3,
        overlap_threshold=0.1,
        min_cohesion=0.5,
    )
    assert clusters == []


def test_cluster_theme_tokens(db):
    """Theme tokens appear in more than 50% of cluster members."""
    # "exception" appears in all 3 items — should be a theme token
    create_item(
        db,
        pattern="catch exception python error handler",
        guidance="always catch exception in python error handling blocks",
        item_type="convention",
        language="python",
        project="myapp",
    )
    create_item(
        db,
        pattern="raise exception python error propagation",
        guidance="always raise exception in python with clear error message",
        item_type="convention",
        language="python",
        project="myapp",
    )
    create_item(
        db,
        pattern="log exception python error traceback",
        guidance="always log exception in python with full traceback error",
        item_type="convention",
        language="python",
        project="myapp",
    )

    clusters = detect_clusters(db, language="python", project="myapp", min_size=3)
    assert len(clusters) == 1
    theme = clusters[0].theme_tokens
    # "exception" and "python" appear in all 3 items — must be theme tokens
    assert "exception" in theme
    assert "python" in theme


# ---------------------------------------------------------------------------
# Evolution lifecycle tests (Task 4): promote_candidate / reject_candidate
# ---------------------------------------------------------------------------

from vidya.evolve import promote_candidate, reject_candidate  # noqa: E402


def _make_source_items(db, count: int = 3, base_confidence: float = 0.5) -> list[str]:
    """Create `count` convention items and return their IDs."""
    ids = []
    for i in range(count):
        item_id = create_item(
            db,
            pattern=f"pattern {i}",
            guidance=f"guidance {i}",
            item_type="convention",
            language="python",
            project="myapp",
            base_confidence=base_confidence,
        )
        ids.append(item_id)
    return ids


def _insert_evolution_candidate(
    db,
    candidate_id: str,
    source_ids: list[str],
    *,
    pattern: str = "test pattern",
    guidance: str = "test guidance",
    scope_language: str | None = "python",
    scope_framework: str | None = None,
    scope_project: str | None = "myapp",
) -> None:
    db.execute(
        """
        INSERT INTO evolution_candidates
            (id, timestamp, pattern, guidance, source_item_ids,
             scope_language, scope_framework, scope_project,
             cluster_theme, cohesion_score, synthesis_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id, "2026-04-10T00:00:00",
            pattern, guidance,
            json.dumps(source_ids),
            scope_language, scope_framework, scope_project,
            "test theme", 0.7, "test-model",
        ),
    )
    db.commit()


def test_promote_creates_bundle(db):
    """promote_candidate creates a knowledge item with type='bundle' and source='evolution'."""
    source_ids = _make_source_items(db, count=3)
    _insert_evolution_candidate(db, "cand-1", source_ids)

    bundle_id = promote_candidate(db, "cand-1")

    assert bundle_id is not None
    row = db.execute(
        "SELECT type, source, related_items FROM knowledge_items WHERE id = ?",
        (bundle_id,),
    ).fetchone()
    assert row is not None, "Bundle item not created"
    assert row["type"] == "bundle"
    assert row["source"] == "evolution"
    related = json.loads(row["related_items"])
    assert set(related) == set(source_ids)


def test_promote_tags_sources(db):
    """All source items get bundle_id set to the new bundle's ID after promotion."""
    source_ids = _make_source_items(db, count=3)
    _insert_evolution_candidate(db, "cand-2", source_ids)

    bundle_id = promote_candidate(db, "cand-2")

    for sid in source_ids:
        row = db.execute(
            "SELECT bundle_id FROM knowledge_items WHERE id = ?", (sid,)
        ).fetchone()
        assert row["bundle_id"] == bundle_id, f"Source {sid} not tagged with bundle_id"


def test_promote_with_edit(db):
    """When edited_guidance is provided, the bundle uses that text instead of the candidate's."""
    source_ids = _make_source_items(db, count=3)
    _insert_evolution_candidate(db, "cand-3", source_ids, guidance="original guidance")

    bundle_id = promote_candidate(db, "cand-3", edited_guidance="improved guidance")

    row = db.execute(
        "SELECT guidance FROM knowledge_items WHERE id = ?", (bundle_id,)
    ).fetchone()
    assert row["guidance"] == "improved guidance"


def test_promote_confidence_averaged(db):
    """Bundle's base_confidence equals the mean of source items' base_confidence values."""
    # Three items with confidences 0.2, 0.4, 0.6 — average = 0.4
    ids = []
    for conf in (0.2, 0.4, 0.6):
        item_id = create_item(
            db,
            pattern="p",
            guidance="g",
            item_type="convention",
            language="python",
            base_confidence=conf,
        )
        ids.append(item_id)

    _insert_evolution_candidate(db, "cand-4", ids)
    bundle_id = promote_candidate(db, "cand-4")

    row = db.execute(
        "SELECT base_confidence FROM knowledge_items WHERE id = ?", (bundle_id,)
    ).fetchone()
    assert abs(row["base_confidence"] - 0.4) < 1e-9


def test_reject_leaves_sources_unchanged(db):
    """reject_candidate sets status='rejected' and does not touch source items."""
    source_ids = _make_source_items(db, count=3)
    _insert_evolution_candidate(db, "cand-5", source_ids)

    reject_candidate(db, "cand-5")

    # Candidate status updated
    row = db.execute(
        "SELECT status FROM evolution_candidates WHERE id = ?", ("cand-5",)
    ).fetchone()
    assert row["status"] == "rejected"

    # Source items untouched — bundle_id still NULL
    for sid in source_ids:
        row = db.execute(
            "SELECT bundle_id FROM knowledge_items WHERE id = ?", (sid,)
        ).fetchone()
        assert row["bundle_id"] is None, f"Source {sid} was unexpectedly modified"
