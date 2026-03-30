# Canon Project — Vidya Seed Rules
# Source: Canon dev skill + observed conventions
# Scope: language=python, project=canon

## Working Directory

- Always run canon CLI and pytest from the project root `/Users/quasi/quasilabs/projects/canon/`
- Never cd into subdirectories when running canon commands — use absolute paths instead
- Always use `uv run` inside `src/cli/` for Python commands, never bare python

## Control Loop

- Always follow the Control Loop: CHECK → PLAN → EXECUTE → VERIFY → RECORD
- Never bypass the control loop for quick fixes
- Before executing any task, answer the Five Questions: what, why, correct looks like, how to know done, what if not done

## Specification (Canon-Specific)

- Always write scenarios before or alongside contracts, never after
- Always write scenarios in pure natural language — never include code blocks in Given/When/Then
- Always link scenarios to contracts via `covers:` field
- Every contract must have at least one covering scenario (avoid dead contracts)
- Always declare `scenarios:` section in feature.yaml with expected count and categories
- Prefer scenario category `happy-path` + `edge-case` + `error-case` coverage minimum per contract

## Testing

- Always run the full test suite before recording a task complete: `cd src/cli && uv run pytest tests/ -v`
- Always use `uv run pytest` not bare `pytest`
- Never commit with failing tests

## Python Conventions

- Always use type hints on public function signatures
- Prefer dataclasses over plain dicts for structured return values
- Never use bare `except Exception` — catch specific exceptions
- Always parameterize SQL queries, never interpolate values into query strings
- Use `with connection:` for atomic multi-statement transactions in SQLite

## Canon DB

- The Canon DB lives at `.canon/canon.db` — never create it elsewhere
- Always verify the correct DB is in use before writing canonical-specification artifacts
- All canonical specification artifacts go inside `canonical-specification/`

## Changelog

- Always update `docs/CHANGELOG.md` after every substantive change
