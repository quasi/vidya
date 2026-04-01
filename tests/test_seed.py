"""Tests for seed.py — import knowledge from markdown files."""

import pytest

from vidya.seed import seed_from_file
from vidya.query import cascade_query


@pytest.fixture
def rules_file(tmp_path):
    """A focused seed file with clear rules."""
    content = """# Python Conventions

## Testing

- Always write tests before implementation (TDD)
- Never commit without all tests passing
- Use pytest for all Python projects

## Error Handling

- Avoid catching bare exceptions
- Always use specific exception types
- Use context managers for resource cleanup
"""
    p = tmp_path / "rules.md"
    p.write_text(content)
    return str(p)


@pytest.fixture
def imperative_file(tmp_path):
    """A file with imperative-verb lines."""
    content = """
Use type hints on all public functions.
Avoid global mutable state.
Prefer composition over inheritance.
Never silence errors with bare except.
"""
    p = tmp_path / "imperatives.md"
    p.write_text(content)
    return str(p)


# --- Basic import ---

def test_seed_returns_count(db, rules_file):
    count = seed_from_file(db, rules_file, language="python")
    assert count > 0


def test_seed_creates_knowledge_items(db, rules_file):
    count = seed_from_file(db, rules_file, language="python")
    actual = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE language = 'python' AND source = 'seed'"
    ).fetchone()[0]
    assert actual == count


def test_seed_items_have_correct_language(db, rules_file):
    seed_from_file(db, rules_file, language="python", project="canon")
    rows = db.execute(
        "SELECT language, project FROM knowledge_items WHERE source = 'seed'"
    ).fetchall()
    for row in rows:
        assert row["language"] == "python"
        assert row["project"] == "canon"


def test_seed_items_have_base_confidence(db, rules_file):
    seed_from_file(db, rules_file, language="python", base_confidence=0.6)
    rows = db.execute(
        "SELECT base_confidence FROM knowledge_items WHERE source = 'seed'"
    ).fetchall()
    for row in rows:
        assert row["base_confidence"] == pytest.approx(0.6)


def test_seed_extracts_bullet_rules(db, rules_file):
    count = seed_from_file(db, rules_file, language="python")
    # "Always write tests before implementation" and similar bullets
    row = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE guidance LIKE '%tests%'"
    ).fetchone()[0]
    assert row >= 1


def test_seed_extracts_imperative_lines(db, imperative_file):
    count = seed_from_file(db, imperative_file, language="python")
    assert count >= 3  # Should extract most of the 4 lines


# --- Deduplication ---

def test_seed_does_not_duplicate_on_re_run(db, rules_file):
    count1 = seed_from_file(db, rules_file, language="python")
    count2 = seed_from_file(db, rules_file, language="python")
    # Second run should create fewer (or zero) new items
    assert count2 <= count1


def test_seed_deduplicates_nearly_identical_rules(db, tmp_path):
    """Two files with the same rules should not double-create items."""
    text = "- Always use type hints on public APIs\n"
    f1 = tmp_path / "a.md"
    f2 = tmp_path / "b.md"
    f1.write_text(text)
    f2.write_text(text)

    seed_from_file(db, str(f1), language="python")
    seed_from_file(db, str(f2), language="python")

    count = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE guidance LIKE '%type hints%'"
    ).fetchone()[0]
    assert count == 1


# --- Seeded items are queryable ---

def test_seeded_items_appear_in_cascade_query(db, rules_file):
    seed_from_file(db, rules_file, language="python", base_confidence=0.6)
    results = cascade_query(db, context="error handling exceptions", language="python")
    assert len(results) >= 1
