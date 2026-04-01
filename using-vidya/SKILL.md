---
name: using-vidya
description: "Procedural learning system — captures your mistakes, user corrections, and non-obvious decisions so you don't repeat errors across sessions. Use whenever: starting any non-trivial task (to retrieve past learnings before repeating past mistakes), the user corrects your approach ('no not that', 'use X instead', 'stop doing Y'), you struggle and switch approaches (tried X, failed, switched to Y), you make a design decision between alternatives, you finish a task, or you see [Vidya knowledge] or [Vidya session brief] injected into your context. This skill makes you better over time — skip it and you repeat the same mistakes every session."
version: 0.3.1
author: quasiLabs
type: workflow
---

# Using Vidya

Vidya watches you work and learns from your mistakes, corrections, and decisions. Over time it builds up rules — "always use X", "never do Y", "when Z happens, try W" — so you stop repeating the same errors across sessions. Your job is to recognize the moments worth capturing and give Vidya quality input.

## What Hooks Handle (Automatic)

Three hooks fire without you doing anything:

| Hook | When | What it does |
|------|------|-------------|
| **SessionStart** | Every session | Runs `vidya brief` — injects knowledge base state + attention items (only if >= 3 items exist) |
| **UserPromptSubmit** | Every prompt (> 15 chars, not `/` commands) | Runs `vidya query` with the user's prompt — injects matching knowledge as `[Vidya knowledge for this task]` |
| **PostToolUse (Bash)** | After any failed bash command | Runs `vidya feedback --type test_failed` — but only if Vidya has relevant items that might relate to the failure |

You do NOT need to manually run `vidya query` for every prompt. The hook handles it. Run `vidya query` explicitly only when you need targeted knowledge on a specific subtopic mid-task.

## What You Must Do (Manual)

The hooks cover knowledge retrieval and error feedback. Everything else is on you:

```
vidya task start  ←  EVERY task. No exceptions.
    │
    ├── vidya step  ←  Non-obvious decisions only
    ├── vidya feedback  ←  User corrections, confirmations
    │
vidya task end  ←  EVERY task. Close the loop.
```

Missing `task start` means no task_id — nothing links together. Missing `task end` means Vidya can't track outcome patterns. Missing feedback on user corrections means the highest-quality learning signal is lost.

## Command Reference

### vidya task start

Call once at the start of every task. Read the returned knowledge items — HIGH-confidence items are earned rules.

```bash
vidya task start \
  --goal "Add error handling to feature X" \
  --language python \
  --project myproject
```

- `--goal` (required) — what you intend to accomplish
- `--language` — always set this
- `--project` — always set when working in a specific project
- `--goal-type` — when obvious: `create`, `modify`, `debug`, `refactor`, `review`

Save the returned task ID — you need it for every subsequent call:

```bash
TASK_ID=$(vidya --json task start --goal "Add error handling" --language python | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
```

### vidya step

Record non-obvious decisions. Skip routine actions.

```bash
vidya step \
  --task-id "$TASK_ID" \
  --action "Chose SQLAlchemy Core over ORM for feature queries" \
  --result "Query works, returns in <50ms" \
  --outcome success \
  --action-type decision \
  --rationale "ORM adds unnecessary abstraction for read-only queries"
```

**Step outcomes:** `success`, `error`, `rejected` (not the same as task end outcomes)

**Action types:**

| Type | When to use | Example |
|------|-------------|---------|
| `tool_call` | Invoked a tool or command | "Ran pytest", "Read config.yaml" |
| `decision` | Chose between alternatives (default) | "Chose SQLAlchemy Core over ORM" |
| `discovery` | Learned something about the codebase | "Found that FTS5 needs manual trigger sync" |
| `correction` | Fixed a mistake or changed approach | "Switched from mock to real DB in tests" |
| `attempt` | Tried something — may succeed or fail | "Tried patching the module directly" |
| `delegation` | Delegated to subagent or process | "Dispatched Haiku agents for 170 scenarios" |
| `configuration` | Changed settings or environment | "Enabled WAL mode on SQLite" |

**Record a step when:**
- You chose between multiple approaches
- You tried something that failed and switched
- You applied a knowledge item and it worked (or didn't)
- You discovered something surprising about the codebase

**Don't record:** routine file reads, standard test runs, obvious edits, trivially-the-only-option actions.

### vidya feedback

The most important tool for learning. Quality here determines whether Vidya gets smarter.

```bash
# After user corrections (highest-quality signal — never skip these)
vidya feedback \
  --type user_correction \
  --detail "Always use uv run python -m pytest, never bare pytest" \
  --language python --project myproject --task-id "$TASK_ID"

# After user confirms a non-obvious approach
vidya feedback \
  --type user_confirmation \
  --detail "Using SQLAlchemy Core not ORM for read queries is correct here" \
  --language python --project myproject --task-id "$TASK_ID"

# After test failures (usually handled by the hook, but use manually for specific lessons)
vidya feedback \
  --type test_failed \
  --detail "CCC scenario.language_independent failed — code found in Given/When/Then" \
  --language python --project myproject --task-id "$TASK_ID"
```

**Feedback types:** `review_accepted`, `review_rejected`, `test_passed`, `test_failed`, `user_correction`, `user_confirmation`

**Writing good detail text** — the `--detail` becomes the item's guidance verbatim. Write the rule you wish existed:

| Good detail | Bad detail |
|------------|-----------|
| `"Always use uv run pytest, never bare pytest"` | `"the tests should use uv"` |
| `"Never put code in Given/When/Then scenarios"` | `"scenarios were wrong"` |
| `"Use with db: for atomic multi-statement SQLite transactions"` | `"database thing was broken"` |

Rules: imperative voice ("Always X" / "Never Y" / "Use Z when W"), name the specific function/file/pattern/command, self-contained so someone reading just this sentence knows what to do.

### vidya task end

```bash
vidya task end \
  --task-id "$TASK_ID" \
  --outcome success
```

**Task outcomes:** `success`, `partial`, `failure`, `abandoned`

On `failure`, also set `--failure-type`: `incomplete`, `constraint_violation`, `wrong_result`, `tool_error`.

### vidya query (manual)

The hook handles per-prompt queries automatically. Use this manually only for targeted lookups:

```bash
vidya query \
  --context "running pytest with uv" \
  --language python --project myproject
```

**Good context strings use specific technical terms:**

| Good | Bad |
|------|-----|
| `"running pytest with uv"` | `"I need to run the tests"` |
| `"error handling SQLite transactions"` | `"dealing with database stuff"` |
| `"FTS5 trigger sync"` | `"making search work"` |

FTS matches on individual words joined with OR. Avoid filler words like "need", "want", "trying", "stuff".

### vidya brief

Structured overview of what Vidya knows. The SessionStart hook injects this automatically, but call manually when you need deeper situational awareness:

```bash
vidya brief --language python --project myproject
```

### vidya maintain

Run periodically for knowledge base hygiene:

```bash
vidya maintain --language python --project myproject
vidya maintain --archive                    # dry-run: show what would be archived
vidya maintain --archive --confirm          # actually archive stale items
```

### vidya explain

Understand an item's history — where it came from, how many confirmations, any failures:

```bash
vidya explain --item-id "<id>"
```

## Confidence Bands

| Band | Threshold | What to do |
|------|-----------|------------|
| HIGH (> 0.5) | Earned through confirmations | Follow this. Deviate only with documented reason. |
| MEDIUM (0.2-0.5) | Not yet proven | Treat as suggestion. Verify before relying on it. |
| LOW (< 0.2) | Stale or unreliable | Not returned in normal queries. Visible via `vidya explain`. |

New items start at 0.15 (LOW) — need ~8 confirmations to reach HIGH. Seeded items start at 0.5-0.6 (HIGH).

## Common Mistakes

| Mistake | Why it matters |
|---------|---------------|
| Skipping `vidya task start` | No task_id = no learning linkage. Nothing connects. |
| Skipping `vidya task end` | Vidya can't track success/failure patterns across tasks. |
| Not recording user corrections | User corrections are the highest-quality learning signal. Always capture them. |
| Vague `--detail` in feedback | "scenarios were wrong" teaches nothing. Write the rule you wished existed. |
| Vague `--context` in query | FTS needs technical terms, not natural language sentences. |
| Recording trivial steps | Noise drowns signal. Only non-obvious decisions. |
| Omitting `--project` | Items lose project scope, weakening project-specific knowledge. |
| Using only `decision` for all steps | Pick the right `--action-type` — Vidya uses these to cluster patterns. |
| Ignoring HIGH-confidence items | They were earned. Follow them unless you have a specific reason not to. |
