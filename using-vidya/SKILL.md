---
name: using-vidya
description: How to use Vidya's CLI effectively — procedural learning that accumulates knowledge across sessions and serves it back
version: 0.2.0
author: quasiLabs
type: workflow
---

# Using Vidya

Vidya accumulates procedural knowledge across sessions and serves it back. It knows what works in your project. Your job is to use it at the right moments and give it quality input.

## The Self-Improvement Loop

```
  Work on task
       |
       v
  vidya task start  -->  Get relevant knowledge + save task_id
       |
       v
  Apply knowledge, make decisions
       |
       v
  vidya step  -->  Log non-obvious decisions
       |
       v
  Outcome happens (success, failure, user correction)
       |
       v
  vidya feedback  -->  Vidya learns from the outcome
       |
       v
  vidya task end  -->  Close the loop
       |
       v
  Next session: better knowledge available
```

Every significant task should have a `vidya task start` at the beginning and a `vidya task end` at the end. The feedback step is what makes Vidya smarter over time.

## Automatic Context Injection

Vidya is wired into Claude Code hooks. On every prompt, `vidya query` runs automatically and injects relevant knowledge into your context — you will see a `[Vidya knowledge for this task]` block. At session start, `vidya brief` injects the knowledge base state and any attention items. When a bash command fails, `vidya feedback --type test_failed` fires automatically if relevant items exist.

You do not need to manually run `vidya query` for every prompt. Do run it explicitly when you need targeted knowledge mid-task on a specific subtopic.

## When to Call Each Tool

### vidya task start

Call once at the start of every task.

```bash
vidya task start \
  --goal "Add error handling to feature X" \
  --language python \
  --project myproject
```

- Always set `--language`. Set `--project` when working in a specific project.
- Set `--goal-type` when obvious: `create`, `modify`, `debug`, `refactor`, `review`.
- **Read the returned knowledge items.** HIGH-confidence items are earned rules. Follow them unless you have a specific reason not to.
- Save the printed `Task: <id>` — you need it for every subsequent call. Use `--json` to capture it programmatically.

```bash
TASK_ID=$(vidya --json task start --goal "Add error handling" --language python | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
```

### vidya query

Call when you need knowledge about a specific subtask or decision. (Also runs automatically on every prompt via the UserPromptSubmit hook.)

```bash
vidya query \
  --context "running pytest with uv" \
  --language python \
  --project myproject
```

**How to write good context strings:**

| Good context | Bad context |
|-------------|-------------|
| `"running pytest with uv"` | `"I need to run the tests"` |
| `"error handling SQLite transactions"` | `"dealing with database stuff"` |
| `"FTS5 trigger sync"` | `"making search work"` |
| `"ASDF dependency management"` | `"checking things"` |

FTS matches on individual words joined with OR. Use specific technical terms. Avoid filler words like "need", "want", "trying", "stuff".

### vidya step

Call for non-obvious decisions. Skip trivial actions.

```bash
vidya step \
  --task-id "$TASK_ID" \
  --action "Chose SQLAlchemy Core over ORM for feature queries" \
  --result "Query works, returns in <50ms" \
  --outcome success \
  --rationale "ORM adds unnecessary abstraction for read-only queries"
```

**When to record a step:**
- You chose between multiple approaches
- You tried something that failed and switched
- You applied a knowledge item and it worked (or didn't)
- You discovered something surprising about the codebase

**When NOT to record a step:**
- Routine file reads, standard test runs, obvious edits
- The action is trivially the only option

### vidya feedback

The most important tool for learning. Quality here determines whether Vidya gets smarter.

**After user corrections:**
```bash
vidya feedback \
  --type user_correction \
  --detail "Always use uv run python -m pytest, never bare pytest" \
  --language python \
  --project myproject \
  --task-id "$TASK_ID"
```

**After user confirms an approach:**
```bash
vidya feedback \
  --type user_confirmation \
  --detail "Using SQLAlchemy Core not ORM for read queries is correct here" \
  --language python \
  --project myproject \
  --task-id "$TASK_ID"
```

**After test failures:**
```bash
vidya feedback \
  --type test_failed \
  --detail "CCC scenario.language_independent failed — code found in Given/When/Then" \
  --language python \
  --project myproject \
  --task-id "$TASK_ID"
```

**How to write good detail text:**

The `--detail` text becomes the item's guidance verbatim. Write the rule you wish existed:

| Good detail | Bad detail |
|------------|-----------|
| `"Always use uv run pytest, never bare pytest"` | `"the tests should use uv"` |
| `"Never put code in Given/When/Then scenarios"` | `"scenarios were wrong"` |
| `"Use with db: for atomic multi-statement SQLite transactions"` | `"database thing was broken"` |

Rules of thumb:
- **Imperative voice**: "Always X" / "Never Y" / "Use Z when W"
- **Specific**: name the function, file, pattern, or command
- **Self-contained**: someone reading just this sentence should know what to do

### vidya task end

```bash
vidya task end \
  --task-id "$TASK_ID" \
  --outcome success
```

Outcomes: `success`, `partial`, `failure`, `abandoned`.

On `failure`, also set `--failure-type`: `incomplete`, `constraint_violation`, `wrong_result`, `tool_error`.

### vidya brief

Call when you want a structured overview of what Vidya knows. Good when you need situational awareness beyond what the automatic hook injected.

```bash
vidya brief --language python --project myproject
```

Returns: item counts by confidence band, items needing attention (never fired, high failure rate, stale), and input quality hints. Use `--json` for machine-readable output.

### vidya explain

```bash
vidya explain --item-id "<id>"
```

Use when you encounter an item and want to know: where did it come from? How many times has it been confirmed? Has it ever failed? Is it being overridden?

## Confidence Bands

| Band | Threshold | What to do |
|------|-----------|------------|
| HIGH (> 0.5) | Earned through confirmations | Follow this guidance. Deviate only with good reason. |
| MEDIUM (0.2-0.5) | Not yet proven | Treat as a suggestion. Verify before relying on it. Confirm via `vidya feedback` if it helps. |
| LOW (< 0.2) | Stale or unreliable | Not returned in normal queries. Visible in `vidya explain`. |

A newly extracted item starts at 0.15 (LOW). It needs ~8 confirmations to reach HIGH. Seeded items start at 0.5-0.6 (HIGH).

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `vidya task start` at task start | No task_id, no learning linkage. Always start a task. |
| Vague `--context` in `vidya query` | Use specific technical terms, not natural language sentences |
| Vague `--detail` in `vidya feedback` | Write the rule you wished existed, in imperative voice |
| Skipping `vidya task end` | Vidya can't track success/failure patterns. Always end the task. |
| Ignoring HIGH-confidence items | They were earned. Follow them unless you have a documented reason. |
| Recording trivial steps | Noise drowns signal. Only record non-obvious decisions. |
| Omitting `--project` | Items get created without project scope, weakening project-specific knowledge |
| Not recording user corrections | User corrections are the highest-quality learning signal. Always capture them. |
