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
uv sync
uv tool install .
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

### 4. Track task lifecycle

```bash
# Start a task — surfaces relevant knowledge
vidya task start --goal "add error handling to API" --language python --project myproject

# Record a step
vidya step --task-id <id> --action "added try/except" --result "tests pass" --outcome success

# Mark complete
vidya task end --task-id <id> --outcome success
```

### 5. Get a situational brief

```bash
vidya brief --language python --project myproject
```

## CLI Reference

```
vidya [--json] seed         Seed knowledge from a markdown rules file
vidya [--json] query        Query relevant knowledge items
vidya [--json] feedback     Record feedback and trigger learning
vidya [--json] task start   Start a task and surface relevant knowledge
vidya [--json] task end     Mark a task complete
vidya [--json] step         Record a step taken during a task
vidya [--json] brief        Structured context dump (items, attention, hints)
vidya [--json] items        List knowledge items
vidya [--json] stats        Show knowledge base statistics
vidya [--json] explain      Show evidence trail for a knowledge item
vidya [--json] evolve       Detect knowledge clusters and synthesize compound rules
vidya [--json] maintain     Health check, stale item detection, optional archival
```

Pass `--json` before any subcommand for machine-readable output:

```bash
vidya --json query --context "error handling" --language python
```

Run `vidya --help` or `vidya <command> --help` for options.

## Knowledge Evolution

As knowledge accumulates, related items cluster. `vidya evolve` detects those clusters and uses an LLM to synthesize them into a single compound rule (a *bundle*). You review the result before it enters the knowledge base.

```bash
# See what clusters exist (no synthesis yet)
vidya evolve --cluster-only --language python --project myproject

# Synthesize candidates (default: min 3 items, overlap 0.35, cohesion 0.35)
vidya evolve --language python --project myproject

# Review pending candidates interactively
vidya evolve --review --project myproject
```

The review prompt offers: `[a]pprove  [e]dit  [r]eject  [s]kip  [q]uit`. Approving a candidate creates a `bundle` knowledge item and links it to its source items. Rejecting leaves the source items unchanged.

**LLM configuration** — synthesis calls an LLM via litellm. Override with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDYA_EVOLVE_MODEL` | `openai/gemma-4-26b-a4b-it-4bit` | litellm model string |
| `VIDYA_EVOLVE_API_BASE` | `http://192.168.1.17:8099/v1` | OpenAI-compatible endpoint |
| `VIDYA_EVOLVE_API_KEY` | `omlx-1234` | Bearer token |

For hosted models, set `VIDYA_EVOLVE_MODEL=claude-haiku-4-5` and leave `API_BASE`/`API_KEY` unset.

**Tuning thresholds** — defaults suit knowledge bases of 100+ items. For smaller bases use looser thresholds:

```bash
vidya evolve --min-size 2 --overlap-threshold 0.3 --min-cohesion 0.3
```

**Bundle decomposition** — if you record a `user_correction` feedback that matches a bundle item, Vidya decomposes the bundle back into its source items so the correction can target the specific rule.

---

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

Starting confidence depends on source: `user_correction` → 0.85 (HIGH immediately), `review_rejected` → 0.65, `user_confirmation` → 0.70. Seeded items start at the `--confidence` value (default 0.5).

## Knowledge Scope

Items are scoped hierarchically. Narrower scope wins in query ranking:

```
global  →  language  →  runtime  →  framework  →  project
                                                  (highest priority)
```

A project-level item overrides a language-level item on the same topic. Items can explicitly override other items by ID, suppressing them from query results.

## Database

SQLite with WAL mode and FTS5 full-text search. Stored at `~/.vidya/vidya.db`. The FTS5 index uses the **Porter stemmer** tokenizer, so "testing" matches "test", "worktrees" matches "worktree", and so on.

Seven tables: `knowledge_items`, `task_records`, `step_records`, `feedback_records`, `extraction_candidates`, `evolution_candidates`, `schema_migrations`. Plus `knowledge_fts` (FTS5 virtual table) and `knowledge_archive` for archived items.

## Project Structure

```
src/vidya/
  schema.py       Database schema, migrations, initialization
  migrate.py      One-time data migration utilities
  store.py        CRUD operations for all tables
  query.py        Cascade query with scope resolution and FTS5 ranking
  confidence.py   Bayesian confidence updates and freshness decay
  learn.py        Feedback-driven knowledge extraction and bundle decomposition
  evolve.py       Cluster detection, LLM synthesis, bundle promotion lifecycle
  brief.py        Structured context dump for vidya brief
  maintain.py     Statistics, health reports, stale item archival
  seed.py         Import knowledge from markdown files
  guidance.py     Agent guidance generation for JSON responses
  cli.py          CLI (Click)
tests/            208 tests
```

## Documentation

- [Reference](docs/reference.md) — Knowledge model, confidence math, CLI tool reference, seed format, database schema
- [Design](docs/design.md) — Architecture, design decisions, what Vidya is and isn't
- [Phase 2 Spec](docs/phase2-spec.md) — Upcoming features and open questions

## Status

Phase 1 complete. 12 source modules, 208 tests, all passing. CLI operational.

Knowledge evolution (`vidya evolve`) is implemented and operational. Phase 2 capacity eviction and drift detection are designed but not yet implemented.

## Author & License

Abhijit Rao (quasi) / quasiLabs / 2026 / [MIT](LICENSE)
