---
name: using-vidya
description: How to use Vidya's MCP tools effectively — procedural learning that accumulates knowledge across sessions and serves it back
version: 0.1.0
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
  vidya_start_task  -->  Get relevant knowledge
       |
       v
  Apply knowledge, make decisions
       |
       v
  vidya_record_step  -->  Log non-obvious decisions
       |
       v
  Outcome happens (success, failure, user correction)
       |
       v
  vidya_feedback  -->  Vidya learns from the outcome
       |
       v
  vidya_end_task  -->  Close the loop
       |
       v
  Next session: better knowledge available
```

Every significant task should have a `vidya_start_task` at the beginning and a `vidya_end_task` at the end. The feedback step is what makes Vidya smarter over time.

## When to Call Each Tool

### vidya_start_task

Call once at the start of every task.

```
vidya_start_task(
  goal="Add error handling to feature X",
  language="python",
  project="myproject"
)
```

- Always set `language`. Set `project` when working in a specific project.
- Set `goal_type` when obvious: `create`, `modify`, `debug`, `refactor`, `review`.
- **Read the returned knowledge items.** HIGH-confidence items are earned rules. Follow them unless you have a specific reason not to.
- Save the `task_id` — you need it for every subsequent call.

### vidya_query

Call when you need knowledge about a specific subtask or decision.

```
vidya_query(
  context="running pytest with uv",
  language="python",
  project="myproject"
)
```

**How to write good context strings:**

| Good context | Bad context |
|-------------|-------------|
| `"running pytest with uv"` | `"I need to run the tests"` |
| `"error handling SQLite transactions"` | `"dealing with database stuff"` |
| `"FTS5 trigger sync"` | `"making search work"` |
| `"ASDF dependency management"` | `"checking things"` |

FTS matches on individual words joined with OR. Use specific technical terms. Avoid filler words like "need", "want", "trying", "stuff".

### vidya_record_step

Call for non-obvious decisions. Skip trivial actions.

```
vidya_record_step(
  task_id="...",
  action="Chose SQLAlchemy Core over ORM for feature queries",
  result="Query works, returns in <50ms",
  outcome="success",
  rationale="ORM adds unnecessary abstraction for read-only queries"
)
```

**When to record a step:**
- You chose between multiple approaches
- You tried something that failed and switched
- You applied a knowledge item and it worked (or didn't)
- You discovered something surprising about the codebase

**When NOT to record a step:**
- Routine file reads, standard test runs, obvious edits
- The action is trivially the only option

### vidya_feedback

The most important tool for learning. Quality here determines whether Vidya gets smarter.

**After user corrections:**
```
vidya_feedback(
  feedback_type="user_correction",
  detail="Always use uv run python -m pytest, never bare pytest",
  language="python",
  project="myproject",
  task_id="..."
)
```

**After user confirms an approach:**
```
vidya_feedback(
  feedback_type="user_confirmation",
  detail="Using SQLAlchemy Core not ORM for read queries is correct here",
  language="python",
  project="myproject",
  task_id="..."
)
```

**After test failures:**
```
vidya_feedback(
  feedback_type="test_failed",
  detail="CCC scenario.language_independent failed — code found in Given/When/Then",
  language="python",
  project="myproject",
  task_id="..."
)
```

**How to write good detail text:**

The `detail` text becomes the item's guidance verbatim. Write the rule you wish existed:

| Good detail | Bad detail |
|------------|-----------|
| `"Always use uv run pytest, never bare pytest"` | `"the tests should use uv"` |
| `"Never put code in Given/When/Then scenarios"` | `"scenarios were wrong"` |
| `"Use with db: for atomic multi-statement SQLite transactions"` | `"database thing was broken"` |

Rules of thumb:
- **Imperative voice**: "Always X" / "Never Y" / "Use Z when W"
- **Specific**: name the function, file, pattern, or command
- **Self-contained**: someone reading just this sentence should know what to do

### vidya_end_task

```
vidya_end_task(
  task_id="...",
  outcome="success"
)
```

Outcomes: `success`, `partial`, `failure`, `abandoned`.

On `failure`, also set `failure_type`: `incomplete`, `constraint_violation`, `wrong_result`, `tool_error`.

### vidya_brief

Call when you want a structured overview of what Vidya knows. Good at session start or when you need situational awareness.

```
vidya_brief(language="python", project="myproject")
```

Returns: item counts by confidence band, items needing attention (never fired, high failure rate, stale), and input quality hints.

### vidya_explain

```
vidya_explain(item_id="...")
```

Use when you encounter an item and want to know: where did it come from? How many times has it been confirmed? Has it ever failed? Is it being overridden?

## Reading _guidance in Responses

Every Vidya tool response includes a `_guidance` field:

```json
{
  "task_id": "...",
  "knowledge": [...],
  "_guidance": {
    "note": "3 HIGH-confidence items returned. 2 never validated.",
    "next_step": "Follow HIGH items. Call vidya_feedback after corrections."
  }
}
```

Read `_guidance.note` for situational awareness. Follow `_guidance.next_step` for what to do next. These are contextual — they change based on the specific response data.

## Confidence Bands

| Band | Threshold | What to do |
|------|-----------|------------|
| HIGH (> 0.5) | Earned through confirmations | Follow this guidance. Deviate only with good reason. |
| MEDIUM (0.2-0.5) | Not yet proven | Treat as a suggestion. Verify before relying on it. Confirm via `vidya_feedback` if it helps. |
| LOW (< 0.2) | Stale or unreliable | Not returned in normal queries. Visible in `vidya_explain`. |

A newly extracted item starts at 0.15 (LOW). It needs ~8 confirmations to reach HIGH. Seeded items start at 0.5-0.6 (HIGH).

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `vidya_start_task` at task start | No task_id, no learning linkage. Always start a task. |
| Vague `context` in `vidya_query` | Use specific technical terms, not natural language sentences |
| Vague `detail` in `vidya_feedback` | Write the rule you wish existed, in imperative voice |
| Skipping `vidya_end_task` | Vidya can't track success/failure patterns. Always end the task. |
| Ignoring HIGH-confidence items | They were earned. Follow them unless you have a documented reason. |
| Recording trivial steps | Noise drowns signal. Only record non-obvious decisions. |
| Omitting `project` | Items get created without project scope, weakening project-specific knowledge |
| Not recording user corrections | User corrections are the highest-quality learning signal. Always capture them. |
