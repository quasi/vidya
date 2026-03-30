"""Seed knowledge from markdown files.

Extracts actionable rules from focused seed files (not raw CLAUDE.md dumps).
Rules are deduplicated against existing items via FTS5 before insertion.
"""

import re
import sqlite3

from vidya.store import create_item
from vidya.learn import classify_type, find_similar_items, overlap_score

_DEFAULT_CONFIDENCE = 0.5
_DEDUP_THRESHOLD = 0.5

# Imperative verbs that signal actionable rules
_IMPERATIVE_VERBS = re.compile(
    r"^(use|avoid|always|never|prefer|ensure|run|add|write|don't|do not|check|"
    r"keep|make|follow|put|place|define|set|return|raise|handle|log|test|import|"
    r"require|enable|disable|configure|call|pass|store|save|load|read|write)\b",
    re.IGNORECASE,
)


def seed_from_file(
    db: sqlite3.Connection,
    file_path: str,
    language: str | None = None,
    runtime: str | None = None,
    framework: str | None = None,
    project: str | None = None,
    base_confidence: float = _DEFAULT_CONFIDENCE,
) -> int:
    """Extract rules from a markdown file and insert as knowledge items.

    Returns the number of new items created (duplicates are skipped).
    All inserts committed in one transaction.
    """
    with open(file_path, encoding="utf-8") as f:
        text = f.read()

    rules = _extract_rules(text)
    created = 0
    with db:
        for rule in rules:
            if _is_duplicate(db, rule, language, project):
                continue
            create_item(
                db,
                pattern=_derive_pattern(rule),
                guidance=rule,
                item_type=classify_type(rule),
                language=language,
                runtime=runtime,
                framework=framework,
                project=project,
                base_confidence=base_confidence,
                source="seed",
                _commit=False,
            )
            created += 1
    return created


def _extract_rules(text: str) -> list[str]:
    """Extract actionable rules from markdown text."""
    rules: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("- ", "* ", "+ ")):
            content = line[2:].strip()
            if _is_rule(content):
                rules.append(content)
            continue
        numbered = re.match(r"^\d+\.\s+(.*)", line)
        if numbered:
            content = numbered.group(1).strip()
            if _is_rule(content):
                rules.append(content)
            continue
        if _IMPERATIVE_VERBS.match(line) and len(line) > 10:
            rules.append(line.rstrip("."))

    # Deduplicate within the file (keep first occurrence)
    seen: set[str] = set()
    unique: list[str] = []
    for r in rules:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _is_rule(text: str) -> bool:
    if len(text) < 8:
        return False
    lower = text.lower()
    return bool(
        _IMPERATIVE_VERBS.match(text)
        or any(kw in lower for kw in ("always", "never", "avoid", "don't", "must", "should", "prefer"))
    )


def _derive_pattern(rule: str) -> str:
    words = [w for w in rule.split() if w.lower() not in
             ("a", "an", "the", "to", "in", "on", "at", "for", "of", "with")]
    return " ".join(words[:6]).rstrip(".,;:")


def _is_duplicate(
    db: sqlite3.Connection,
    rule: str,
    language: str | None,
    project: str | None,
) -> bool:
    similar = find_similar_items(db, rule, language, project)
    return any(overlap_score(rule, item) >= _DEDUP_THRESHOLD for item in similar)
