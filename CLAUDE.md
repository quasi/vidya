# Vidya Project

## Session Startup

At the start of every Vidya session, ask: "Run maintenance and evolution before we start?" If confirmed:

1. `vidya maintain` — deduplication, staleness pruning, confidence decay
2. `vidya evolve` — LLM synthesis of related items into compound rules

Run them in order (maintain first, then evolve). Report a one-line summary of what changed.

## Package Management

This project uses **uv** — never use `pip` directly.

- `uv sync` — install/update dependencies into local venv (dev workflow)
- `uv tool install . --reinstall` — reinstall the CLI globally
