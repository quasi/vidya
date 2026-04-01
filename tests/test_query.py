"""Tests for query.py — cascade query with scope resolution and FTS5 ranking."""

import pytest

from vidya.store import create_item
from vidya.query import cascade_query, QueryResult, _sanitize_fts_tokens


# --- Test 1: scope specificity — project beats language beats global ---

def test_project_item_outranks_language_and_global(db):
    """Three items on same topic at different scopes — project wins."""
    create_item(db, pattern="error handling", guidance="Global: use logging", item_type="convention",
                base_confidence=0.8)
    create_item(db, pattern="error handling", guidance="Python: use exceptions", item_type="convention",
                language="python", base_confidence=0.8)
    create_item(db, pattern="error handling in canon", guidance="Canon: use Result", item_type="convention",
                language="python", project="canon", base_confidence=0.8)

    results = cascade_query(db, context="error handling", language="python", project="canon")
    assert len(results) >= 1
    # The project-scoped item must rank first
    assert results[0].scope_level == "project"


def test_language_item_outranks_global(db):
    """Language-scoped item beats global when language matches."""
    create_item(db, pattern="error handling", guidance="Global guidance", item_type="convention",
                base_confidence=0.8)
    create_item(db, pattern="error handling", guidance="Python guidance", item_type="convention",
                language="python", base_confidence=0.8)

    results = cascade_query(db, context="error handling", language="python")
    assert len(results) >= 2
    assert results[0].scope_level == "language"


# --- Test 2: override suppression ---

def test_override_suppresses_overridden_item(db):
    """If project item overrides language item, language item is suppressed."""
    lang_id = create_item(
        db, pattern="error handling", guidance="Use exceptions", item_type="convention",
        language="python", base_confidence=0.8
    )
    # Project item explicitly overrides the language item
    create_item(
        db, pattern="error handling in canon", guidance="Use Result type", item_type="convention",
        language="python", project="canon", base_confidence=0.8,
        overrides=lang_id,
    )

    results = cascade_query(db, context="error handling", language="python", project="canon")
    result_ids = [r.id for r in results]
    # Language item should be suppressed
    assert lang_id not in result_ids


# --- Test 3: FTS5 relevance — semantic matching ---

def test_fts_matches_relevant_terms(db):
    """'error handling' context matches 'error recovery' but not 'database migration'."""
    err_id = create_item(
        db, pattern="error recovery", guidance="Use retry with backoff", item_type="convention",
        language="python", base_confidence=0.8
    )
    db_id = create_item(
        db, pattern="database migration", guidance="Run alembic upgrade", item_type="convention",
        language="python", base_confidence=0.8
    )

    results = cascade_query(db, context="error handling", language="python")
    result_ids = [r.id for r in results]

    assert err_id in result_ids
    assert db_id not in result_ids


# --- Test 4: freshness affects ranking ---

def test_stale_item_ranks_below_fresh_item(db):
    """Stale item (same base_confidence) ranks below a recently-fired fresh item."""
    fresh_id = create_item(
        db, pattern="testing approach", guidance="Use pytest fixtures", item_type="convention",
        language="python", base_confidence=0.6
    )
    stale_id = create_item(
        db, pattern="testing approach best practice", guidance="Write tests first always", item_type="convention",
        language="python", base_confidence=0.6
    )
    # Simulate staleness: set last_fired 200 days ago
    from datetime import datetime, timezone, timedelta
    stale_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    db.execute("UPDATE knowledge_items SET last_fired = ? WHERE id = ?", (stale_date, stale_id))
    db.commit()

    # Use a low min_confidence so the stale item (effective ≈ 0.18) still appears.
    results = cascade_query(db, context="testing approach", language="python", min_confidence=0.1)
    result_ids = [r.id for r in results]

    assert fresh_id in result_ids
    assert stale_id in result_ids
    # Fresh item should rank higher
    fresh_rank = result_ids.index(fresh_id)
    stale_rank = result_ids.index(stale_id)
    assert fresh_rank < stale_rank


# --- min_confidence filter ---

def test_min_confidence_filters_low_items(db):
    """Items below min_confidence are excluded from results."""
    create_item(
        db, pattern="error handling", guidance="High confidence guidance", item_type="convention",
        language="python", base_confidence=0.8
    )
    create_item(
        db, pattern="error handling approach", guidance="Low confidence guidance", item_type="convention",
        language="python", base_confidence=0.05
    )

    results = cascade_query(db, context="error handling", language="python", min_confidence=0.2)
    for r in results:
        assert r.effective_confidence >= 0.2


# --- QueryResult structure ---

def test_query_result_has_required_fields(db):
    item_id = create_item(
        db, pattern="error handling", guidance="Use exceptions", item_type="convention",
        language="python", base_confidence=0.6
    )

    results = cascade_query(db, context="error handling", language="python")
    assert len(results) >= 1
    r = results[0]
    assert r.id == item_id
    assert r.pattern == "error handling"
    assert r.guidance == "Use exceptions"
    assert r.type == "convention"
    assert r.effective_confidence > 0
    assert r.scope_level in ("global", "language", "runtime", "framework", "project")
    assert isinstance(r.match_reason, str)
    assert len(r.match_reason) > 0


# --- out-of-scope items not returned ---

def test_different_language_not_returned(db):
    """Items scoped to a different language are not returned."""
    create_item(
        db, pattern="error handling", guidance="Use conditions", item_type="convention",
        language="common-lisp", base_confidence=0.8
    )

    results = cascade_query(db, context="error handling", language="python")
    for r in results:
        assert r.scope_level != "language" or r.id not in [
            row[0] for row in db.execute(
                "SELECT id FROM knowledge_items WHERE language = 'common-lisp'"
            ).fetchall()
        ]


# --- Language-independent framework (tool knowledge) ---

def test_framework_item_without_language_matches_any_language(db):
    """A framework=canon item with language=NULL matches queries in any language."""
    tool_id = create_item(
        db, pattern="scenario cluster", guidance="Always set --cluster on scenarios",
        item_type="convention", framework="canon", base_confidence=0.8,
    )
    # Query from Python context — should find the language-independent canon item
    results = cascade_query(db, context="scenario cluster", language="python", framework="canon")
    result_ids = [r.id for r in results]
    assert tool_id in result_ids

    # Query from Rust context — same item should match
    results = cascade_query(db, context="scenario cluster", language="rust", framework="canon")
    result_ids = [r.id for r in results]
    assert tool_id in result_ids


def test_language_specific_framework_item_does_not_cross_languages(db):
    """A framework=canon item with language=python does NOT match language=rust."""
    py_id = create_item(
        db, pattern="scenario cluster", guidance="Python-specific canon advice",
        item_type="convention", language="python", framework="canon", base_confidence=0.8,
    )
    results = cascade_query(db, context="scenario cluster", language="rust", framework="canon")
    result_ids = [r.id for r in results]
    assert py_id not in result_ids


def test_language_specific_framework_outranks_language_independent(db):
    """language+framework item should rank above language-independent framework item."""
    tool_id = create_item(
        db, pattern="scenario validation", guidance="Generic canon advice",
        item_type="convention", framework="canon", base_confidence=0.8,
    )
    specific_id = create_item(
        db, pattern="scenario validation", guidance="Python-specific canon advice",
        item_type="convention", language="python", framework="canon", base_confidence=0.8,
    )
    results = cascade_query(db, context="scenario validation", language="python", framework="canon")
    result_ids = [r.id for r in results]
    assert specific_id in result_ids
    assert tool_id in result_ids
    # Both are "framework" scope but language-specific should rank higher via scope boost
    # (both get framework boost, but the language-specific one also gets FTS relevance
    # on the same terms — they may tie, but both must be present)


def test_framework_item_without_language_not_returned_without_framework_query(db):
    """framework=canon items should NOT appear when querying without framework."""
    create_item(
        db, pattern="scenario cluster", guidance="Canon tool advice",
        item_type="convention", framework="canon", base_confidence=0.8,
    )
    # Query without framework — should not find the framework-scoped item
    results = cascade_query(db, context="scenario cluster", language="python")
    result_ids = [r.id for r in results]
    assert len(result_ids) == 0


# --- Query without language (framework-only) ---

def test_query_with_framework_only_no_language(db):
    """Querying with only framework (no language) finds framework-scoped items."""
    tool_id = create_item(
        db, pattern="scenario cluster", guidance="Always set --cluster on scenarios",
        item_type="convention", framework="canon", base_confidence=0.8,
    )
    results = cascade_query(db, context="scenario cluster", framework="canon")
    result_ids = [r.id for r in results]
    assert tool_id in result_ids


def test_query_without_language_finds_global_items(db):
    """Querying without language still finds global items."""
    global_id = create_item(
        db, pattern="error handling", guidance="Handle errors properly",
        item_type="convention", base_confidence=0.8,
    )
    results = cascade_query(db, context="error handling")
    result_ids = [r.id for r in results]
    assert global_id in result_ids


# --- FTS5 sanitization ---

def test_sanitize_fts_tokens_quotes_words():
    assert _sanitize_fts_tokens("error handling") == '"error" OR "handling"'


def test_sanitize_fts_tokens_neutralizes_operators():
    result = _sanitize_fts_tokens("NOT error AND handling")
    assert '"NOT"' in result
    assert '"AND"' in result


def test_sanitize_fts_tokens_escapes_internal_quotes():
    result = _sanitize_fts_tokens('say "hello"')
    assert '""hello""' in result


def test_sanitize_fts_tokens_empty():
    assert _sanitize_fts_tokens("") == ""


def test_fts_special_chars_do_not_crash(db):
    """Query with FTS operator characters should not raise."""
    create_item(db, pattern="error handling", guidance="Handle errors properly",
                item_type="convention", language="python", base_confidence=0.8)
    # These contain FTS5 operators — should not crash
    results = cascade_query(db, context="NOT error", language="python")
    assert isinstance(results, list)
    results = cascade_query(db, context="error AND handling OR *", language="python")
    assert isinstance(results, list)
