# Vidya

*Vidya (विद्या) — procedural learning system for AI coding agents.*

Vidya observes agent work, extracts procedural knowledge, and serves it back. Knowledge lives in a SQLite database, not in any agent's context. It survives across sessions, machines, and tool changes.

An agent that uses Vidya gets better at your project over time — without fine-tuning, embeddings, or external services.

## How It Works

```
Session 1:  Agent makes mistake → User corrects → Vidya stores the rule
Session 2:  Agent starts task → Vidya serves the rule → Agent avoids the mistake
Session 3:  Rule confirmed again → Confidence rises → Rule becomes trusted
```

Knowledge items have a **confidence score** that rises with confirmations and decays with failures or staleness. Items that prove themselves earn HIGH confidence. Items that fail drop quickly. Unused items fade over time.

## Install

Requires Python 3.12+.

```bash
git clone https://github.com/quasi/vidya.git
cd vidya
pip install -e ".[dev]"
```

## Quickstart

### 1. Seed knowledge from a markdown file

Write rules in a markdown file:

```markdown
# Python Conventions

- Always use type hints on public functions
- Never catch bare exceptions
- Use pytest for all Python projects
```

Then seed them:

```bash
vidya seed --file rules.md --language python --project myproject
```

### 2. Query relevant knowledge

```bash
vidya query --language python --context "error handling exceptions"
```

Output:

```
[HIGH 0.60] [convention] [language]
  Pattern:  Always use specific exception types
  Guidance: Always use specific exception types
  Reason:   scope=language, language=python, fts_match=1.00
```

### 3. Record feedback to teach Vidya

```bash
vidya feedback \
  --type user_correction \
  --detail "Always use uv run pytest, never bare pytest" \
  --language python \
  --project myproject
```

This creates a new knowledge item at LOW confidence. Repeat confirmations raise it to HIGH.

## Interfaces

Vidya is a Python library with two thin wrappers. No business logic lives in the interface layer.

### MCP Server

For AI agents that support the [Model Context Protocol](https://modelcontextprotocol.io/):

```bash
vidya-server
```

Exposes 8 tools: `vidya_start_task`, `vidya_end_task`, `vidya_record_step`, `vidya_query`, `vidya_feedback`, `vidya_explain`, `vidya_stats`, `vidya_brief`.

Every MCP response includes a `_guidance` field with a contextual `note` and `next_step` — the agent reads these to know what to do next.

### CLI

For humans and scripts:

```
vidya seed       Seed knowledge from a markdown rules file
vidya query      Query relevant knowledge items
vidya feedback   Record feedback and trigger learning
vidya items      List knowledge items
vidya stats      Show knowledge base statistics
vidya explain    Show evidence trail for a knowledge item
```

Run `vidya --help` or `vidya <command> --help` for options.

## Confidence Model

Two-field model per item:

| Field | Description |
|-------|-------------|
| `base_confidence` | Epistemic trust (0-1). Rises slowly with confirmations (+0.05 asymptotic), drops quickly on failure (x0.70). |
| `freshness` | Temporal staleness. Computed at query time from days since last fired. Decays 0.5%/day, floors at 0.3. |

**Effective confidence** = base_confidence x freshness. This is what queries rank by.

| Band | Threshold | Meaning |
|------|-----------|---------|
| HIGH | > 0.5 | Earned through confirmations. Trusted. |
| MEDIUM | 0.2 - 0.5 | Not yet proven. Treat as suggestion. |
| LOW | < 0.2 | Stale or unreliable. Filtered from default queries. |

A new item from feedback starts at 0.15. It takes ~8 confirmations to reach HIGH. Seeded items start at 0.5-0.6.

## Knowledge Scope

Items are scoped hierarchically. Narrower scope wins in query ranking:

```
global  →  language  →  runtime  →  framework  →  project
                                                  (highest priority)
```

A project-level item overrides a language-level item on the same topic. Items can explicitly override other items by ID, suppressing them from query results.

## Database

SQLite with WAL mode and FTS5 full-text search. Stored at `~/.vidya/vidya.db`.

Five tables: `knowledge_items`, `task_records`, `step_records`, `feedback_records`, `extraction_candidates`. Plus `knowledge_fts` (FTS5 virtual table) and `knowledge_archive` for archived items.

## Project Structure

```
src/vidya/
  schema.py       Database schema and initialization
  store.py        CRUD operations for all tables
  query.py        Cascade query with scope resolution and FTS5 ranking
  confidence.py   Bayesian confidence updates and freshness decay
  learn.py        Feedback-driven knowledge extraction
  brief.py        Structured context dump for vidya_brief
  guidance.py     Contextual guidance for MCP responses
  maintain.py     Statistics computation
  seed.py         Import knowledge from markdown files
  cli.py          CLI (Click)
  mcp_server.py   MCP server (stdio)
tests/            106 tests
```

## Documentation

- [Reference](docs/reference.md) — Knowledge model, confidence math, CLI/MCP tool reference, seed format, database schema
- [Design](docs/design.md) — Architecture, design decisions, what Vidya is and isn't
- [Phase 2 Spec](docs/phase2-spec.md) — Upcoming features and open questions

## Status

Phase 1 complete. 12 source modules, 106 tests, all passing. MCP server and CLI both operational.

Phase 2 (capacity eviction, drift detection, pattern-based extraction) is designed but not yet implemented.

## Author & License

Abhijit Rao (quasi) / quasiLabs / 2026 / [MIT](LICENSE)


