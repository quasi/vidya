#!/usr/bin/env python3
"""SessionStart hook — run vidya brief and inject as a system-level context reminder.

Only fires when the DB has items worth reporting (>= 3). Detects language from CWD.
Attention items (never-fired, high-failure, stale) are the key signal here —
the UserPromptSubmit hook already handles per-prompt knowledge injection.
"""

import json
import os
import subprocess
import sys


def detect_project(cwd: str) -> str | None:
    if not cwd or not os.path.isdir(cwd):
        return None
    return os.path.basename(cwd.rstrip("/")) or None


def detect_language(cwd: str) -> str | None:
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        files = set(os.listdir(cwd))
    except OSError:
        return None
    if files & {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"}:
        return "python"
    if "Cargo.toml" in files:
        return "rust"
    if any(f.endswith(".asd") for f in files):
        return "common-lisp"
    if "package.json" in files:
        return "typescript"
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    cwd = data.get("cwd", "")
    language = detect_language(cwd)

    cmd = ["vidya", "--json", "brief"]
    if language:
        cmd += ["--language", language]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        brief = json.loads(proc.stdout)
    except Exception:
        sys.exit(0)

    state = brief.get("project_state", {})
    total = state.get("total_items", 0)

    # Not worth injecting if the DB has nothing meaningful
    if total < 3:
        sys.exit(0)

    high = state.get("high", 0)
    medium = state.get("medium", 0)
    low = state.get("low", 0)
    never_fired = state.get("never_fired", 0)
    total_tasks = state.get("total_tasks", 0)
    total_feedback = state.get("total_feedback", 0)
    last_outcome = state.get("last_task_outcome")

    lines = [
        "[Vidya session brief]",
        f"Knowledge: {total} items — HIGH={high} MED={medium} LOW={low}",
        f"Tasks: {total_tasks}  Feedback: {total_feedback}",
    ]
    if last_outcome:
        lines.append(f"Last task outcome: {last_outcome}")
    if never_fired:
        lines.append(f"Note: {never_fired} item(s) have never been validated in practice.")

    attention = brief.get("attention_items", [])
    if attention:
        lines.append(f"Attention ({len(attention)} item(s) need review):")
        for item in attention[:5]:
            lines.append(f"  · {item['pattern'][:70]}")
            lines.append(f"    {item['reason']}")

    context_text = "\n".join(lines)

    print(json.dumps({"additionalContext": context_text}))


if __name__ == "__main__":
    main()
