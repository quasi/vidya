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
    """Components whose average pairwise overlap is below min_cohesion are rejected.

    Strategy: create 3 items that form a chain (A-B connected, B-C connected, A-C not)
    via a shared bridging token. The component has size 3 (passes min_size) but low
    cohesion since most pairs have minimal overlap. Verify it's rejected by cohesion,
    then accepted when min_cohesion is lowered.
    """
    # A and B share "config" bridge token; B and C share "setup" bridge token;
    # A and C share nothing beyond stopwords.
    create_item(db, pattern="config alpha bravo charlie delta",
                guidance="config alpha bravo charlie delta echo",
                item_type="convention", language="python", project="cohesion")
    create_item(db, pattern="config setup foxtrot golf hotel",
                guidance="config setup foxtrot golf hotel india",
                item_type="convention", language="python", project="cohesion")
    create_item(db, pattern="setup juliet kilo lima mike",
                guidance="setup juliet kilo lima mike november",
                item_type="convention", language="python", project="cohesion")

    # At threshold=0.1 all three connect (each pair shares at least 1/10 tokens).
    # But cohesion is low (A-C overlap ≈ 0) → average pairwise well below 0.5.
    clusters = detect_clusters(
        db, language="python", project="cohesion",
        min_size=3, overlap_threshold=0.1, min_cohesion=0.5,
    )
    assert clusters == [], "Cohesion gate should reject low-cohesion component"

    # With min_cohesion lowered, the same component should be accepted.
    clusters_low = detect_clusters(
        db, language="python", project="cohesion",
        min_size=3, overlap_threshold=0.1, min_cohesion=0.05,
    )
    assert len(clusters_low) == 1, "Component should pass with low cohesion threshold"


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


# ---------------------------------------------------------------------------
# Compound Synthesis tests (Task 3): synthesize_cluster
# ---------------------------------------------------------------------------

from unittest.mock import patch, MagicMock  # noqa: E402

from vidya.evolve import synthesize_cluster, EvolutionCandidate  # noqa: E402
from vidya.store import create_item  # noqa: E402 (already imported above, but re-import is fine)


def _make_cluster_with_items(db, count: int = 3) -> tuple:
    """Create `count` items and return (cluster, items_list)."""
    from vidya.evolve import Cluster

    ids = []
    items = []
    for i in range(count):
        iid = create_item(
            db,
            pattern=f"use async pattern {i}",
            guidance=f"always await coroutine calls in async context pattern {i}",
            item_type="convention",
            language="python",
            framework="django",
            project="webapp",
        )
        ids.append(iid)
        items.append({
            "id": iid,
            "pattern": f"use async pattern {i}",
            "guidance": f"always await coroutine calls in async context pattern {i}",
        })

    cluster = Cluster(
        item_ids=ids,
        scope={"language": "python", "framework": "django", "project": "webapp"},
        cohesion=0.75,
        theme_tokens=["async", "pattern"],
    )
    return cluster, items


def _mock_litellm_response(pattern: str, guidance: str) -> MagicMock:
    """Build a MagicMock that mimics a litellm completion response."""
    import json
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(
        {"pattern": pattern, "guidance": guidance}
    )
    return mock_response


def test_synthesize_happy_path(db):
    """synthesize_cluster creates a pending evolution_candidate in DB with correct fields."""
    cluster, items = _make_cluster_with_items(db)
    synth_pattern = "use async await in django"
    synth_guidance = (
        "always await coroutine calls in async context; "
        "use async def views and async orm in django"
    )
    mock_resp = _mock_litellm_response(synth_pattern, synth_guidance)

    with patch("litellm.completion", return_value=mock_resp) as mock_call:
        result = synthesize_cluster(cluster, items, db, model="test-model")

    assert result is not None
    assert isinstance(result, EvolutionCandidate)
    assert result.pattern == synth_pattern
    assert result.guidance == synth_guidance
    assert result.cluster_theme == "async, pattern"
    assert result.cohesion_score == 0.75
    assert set(result.source_item_ids) == set(cluster.item_ids)

    # Verify DB row
    row = db.execute(
        "SELECT * FROM evolution_candidates WHERE id = ?", (result.id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["synthesis_model"] == "test-model"
    assert row["scope_language"] == "python"
    assert row["scope_framework"] == "django"
    assert row["scope_project"] == "webapp"
    assert json.loads(row["source_item_ids"]) == cluster.item_ids

    mock_call.assert_called_once()


def test_synthesize_llm_unavailable(db):
    """When litellm.completion raises, synthesize_cluster returns None and inserts nothing."""
    cluster, items = _make_cluster_with_items(db)

    with patch("litellm.completion", side_effect=Exception("connection refused")):
        result = synthesize_cluster(cluster, items, db)

    assert result is None

    rows = db.execute("SELECT COUNT(*) as n FROM evolution_candidates").fetchone()
    assert rows["n"] == 0


def test_synthesize_short_output_flagged(db):
    """Guidance shorter than shortest source item's guidance triggers review_notes warning."""
    cluster, items = _make_cluster_with_items(db)
    # Very short guidance — fewer words than any source item
    mock_resp = _mock_litellm_response("use async", "await calls")

    with patch("litellm.completion", return_value=mock_resp):
        result = synthesize_cluster(cluster, items, db)

    assert result is not None
    assert result.review_notes is not None
    assert "shorter than shortest source" in result.review_notes

    # DB row should also have the review_notes set
    row = db.execute(
        "SELECT review_notes FROM evolution_candidates WHERE id = ?", (result.id,)
    ).fetchone()
    assert row["review_notes"] is not None
    assert "shorter than shortest source" in row["review_notes"]


def test_synthesize_json_retry(db):
    """First call returns invalid JSON; second call returns valid JSON; result succeeds."""
    import json as _json

    cluster, items = _make_cluster_with_items(db)
    synth_guidance = (
        "always await coroutine calls in async context using async def pattern"
    )

    bad_response = MagicMock()
    bad_response.choices[0].message.content = "not valid json {"

    good_response = _mock_litellm_response("use async safely", synth_guidance)

    with patch("litellm.completion", side_effect=[bad_response, good_response]) as mock_call:
        result = synthesize_cluster(cluster, items, db)

    assert result is not None
    assert result.pattern == "use async safely"
    assert mock_call.call_count == 2

    # The second call should include the retry suffix
    second_call_messages = mock_call.call_args_list[1][1]["messages"]
    user_content = second_call_messages[-1]["content"]
    assert "respond with valid JSON only" in user_content


# ---------------------------------------------------------------------------
# Decomposition tests (Task 5): decompose_bundle
# ---------------------------------------------------------------------------

from vidya.evolve import decompose_bundle  # noqa: E402


def _setup_promoted_bundle(db) -> tuple[str, list[str]]:
    """Create source items + evolution candidate, promote, return (bundle_id, source_ids)."""
    source_ids = _make_source_items(db, count=3)
    _insert_evolution_candidate(db, "cand-decomp", source_ids)
    bundle_id = promote_candidate(db, "cand-decomp")
    return bundle_id, source_ids


def test_decompose_clears_bundle_id(db):
    """After decompose_bundle, all source items have bundle_id = NULL."""
    bundle_id, source_ids = _setup_promoted_bundle(db)

    # Verify sources are tagged before decomposition
    for sid in source_ids:
        row = db.execute("SELECT bundle_id FROM knowledge_items WHERE id = ?", (sid,)).fetchone()
        assert row["bundle_id"] == bundle_id, f"Precondition: {sid} should have bundle_id set"

    decompose_bundle(db, bundle_id)

    for sid in source_ids:
        row = db.execute("SELECT bundle_id FROM knowledge_items WHERE id = ?", (sid,)).fetchone()
        assert row["bundle_id"] is None, f"Source {sid} still has bundle_id after decomposition"


def test_decompose_supersedes_bundle(db):
    """After decompose_bundle, the bundle item has status = 'superseded'."""
    bundle_id, _ = _setup_promoted_bundle(db)

    decompose_bundle(db, bundle_id)

    row = db.execute("SELECT status FROM knowledge_items WHERE id = ?", (bundle_id,)).fetchone()
    assert row["status"] == "superseded"


def test_decompose_returns_source_ids(db):
    """decompose_bundle returns the list of source item IDs stored in related_items."""
    bundle_id, source_ids = _setup_promoted_bundle(db)

    returned = decompose_bundle(db, bundle_id)

    assert set(returned) == set(source_ids)
    assert len(returned) == len(source_ids)


# ---------------------------------------------------------------------------
# learn.py integration test (Task 5): feedback on bundle triggers decomposition
# ---------------------------------------------------------------------------

from vidya.learn import extract_from_feedback  # noqa: E402


def _make_feedback_record(fb_id: str, detail: str, language: str = "python") -> dict:
    """Minimal feedback dict for extract_from_feedback."""
    return {
        "id": fb_id,
        "feedback_type": "user_correction",
        "detail": detail,
        "language": language,
        "project": "myapp",
        "framework": None,
        "runtime": None,
    }


def test_feedback_on_bundle_triggers_decomposition(db):
    """When a correction matches a bundle item, decompose_bundle is called.

    Result must contain decomposed=True and the bundle must be superseded.
    """
    # Create source items with high-overlap text so the bundle is findable via FTS
    shared_text = "always use async await coroutines in django views python"
    source_ids = []
    for i in range(3):
        sid = create_item(
            db,
            pattern=f"async django pattern {i}",
            guidance=shared_text,
            item_type="convention",
            language="python",
            project="myapp",
        )
        source_ids.append(sid)

    # Create and promote an evolution candidate
    _insert_evolution_candidate(
        db,
        "cand-fb",
        source_ids,
        pattern="async django await pattern",
        guidance=shared_text,
        scope_language="python",
        scope_project="myapp",
    )
    bundle_id = promote_candidate(db, "cand-fb")

    # Verify setup: bundle item exists and sources are tagged
    bundle_row = db.execute(
        "SELECT type, status FROM knowledge_items WHERE id = ?", (bundle_id,)
    ).fetchone()
    assert bundle_row["type"] == "bundle"
    assert bundle_row["status"] == "active"

    # Submit a correction that has heavy overlap with the bundle's pattern/guidance
    feedback = _make_feedback_record("fb-001", shared_text)
    result = extract_from_feedback(db, feedback)

    # Result must signal decomposition
    assert result is not None, "extract_from_feedback returned None unexpectedly"
    assert result.get("decomposed") is True, f"Expected decomposed=True, got: {result}"
    assert result.get("bundle_id") == bundle_id
    assert set(result.get("source_ids", [])) == set(source_ids)

    # Side effect: bundle must be superseded
    bundle_row = db.execute(
        "SELECT status FROM knowledge_items WHERE id = ?", (bundle_id,)
    ).fetchone()
    assert bundle_row["status"] == "superseded", "Bundle should be superseded after decomposition"

    # Side effect: source items must have bundle_id cleared
    for sid in source_ids:
        row = db.execute("SELECT bundle_id FROM knowledge_items WHERE id = ?", (sid,)).fetchone()
        assert row["bundle_id"] is None, f"Source {sid} should have bundle_id cleared"


# ---------------------------------------------------------------------------
# Query Presentation Grouping tests (Task 6)
# ---------------------------------------------------------------------------

from vidya.query import cascade_query, QueryResult  # noqa: E402


def _make_bundled_items(db) -> tuple[list[str], str]:
    """Create 3 items about error handling and bundle them. Returns (source_ids, bundle_id)."""
    source_ids = []
    for i in range(3):
        iid = create_item(
            db,
            pattern=f"error handling python exception catch retry {i}",
            guidance=f"always handle errors with try except blocks and retry logic python {i}",
            item_type="convention",
            language="python",
            base_confidence=0.6,
        )
        source_ids.append(iid)

    # Create bundle directly (Option 2 from task description)
    bundle_id = create_item(
        db,
        pattern="error handling python exception catch retry bundle",
        guidance="bundle: handle errors with try except blocks retry logic python comprehensive",
        item_type="bundle",
        language="python",
        base_confidence=0.6,
        source="evolution",
    )

    # Tag each source item with bundle_id
    for sid in source_ids:
        update_item(db, sid, bundle_id=bundle_id)

    return source_ids, bundle_id


def test_query_groups_bundled_items(db):
    """When items share a bundle_id and the bundle is active, query returns one result."""
    source_ids, bundle_id = _make_bundled_items(db)

    results = cascade_query(
        db,
        context="error handling python exception catch retry",
        language="python",
    )

    # Only the bundle result should appear — individual source items collapsed
    result_ids = [r.id for r in results]
    assert bundle_id in result_ids, "Bundle item should appear in results"

    for sid in source_ids:
        assert sid not in result_ids, f"Source item {sid} should be collapsed into bundle"

    bundle_result = next(r for r in results if r.id == bundle_id)
    assert bundle_result.match_source == "bundle"
    assert bundle_result.bundle_member_count == 3


def test_query_ungrouped_items_unchanged(db):
    """Items without bundle_id pass through results unchanged (no match_source, no count)."""
    for i in range(2):
        create_item(
            db,
            pattern=f"database query optimisation index performance {i}",
            guidance=f"always use indexes for database query performance optimisation {i}",
            item_type="convention",
            language="python",
            base_confidence=0.7,
        )

    results = cascade_query(
        db,
        context="database query optimisation index performance",
        language="python",
    )

    assert len(results) >= 1
    for r in results:
        assert r.match_source is None, "Ungrouped item should have no match_source"
        assert r.bundle_member_count is None, "Ungrouped item should have no bundle_member_count"


def test_query_after_decomposition_no_grouping(db):
    """After a bundle is superseded (decomposed), individual items are returned ungrouped."""
    source_ids, bundle_id = _make_bundled_items(db)

    # Simulate decomposition: mark bundle as superseded, clear bundle_id on sources
    update_item(db, bundle_id, status="superseded")
    for sid in source_ids:
        update_item(db, sid, bundle_id=None)

    results = cascade_query(
        db,
        context="error handling python exception catch retry",
        language="python",
    )

    result_ids = [r.id for r in results]

    # Bundle should NOT appear (it's superseded, not active)
    assert bundle_id not in result_ids, "Superseded bundle should not appear"

    # Source items should appear ungrouped
    for sid in source_ids:
        assert sid in result_ids, f"Source item {sid} should appear after decomposition"

    for r in results:
        assert r.match_source is None, "No grouping after decomposition"
        assert r.bundle_member_count is None
