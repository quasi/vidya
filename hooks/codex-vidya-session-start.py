#!/usr/bin/env python3
"""Codex SessionStart hook — run vidya brief and inject session context."""

import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

LOG_PATH = "/Users/quasi/.codex/hooks/vidya-hooks.log"


def detect_project(cwd: str) -> Optional[str]:
    if not cwd or not os.path.isdir(cwd):
        return None
    return os.path.basename(cwd.rstrip("/")) or None


def detect_language(cwd: str) -> Optional[str]:
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


def log_line(message: str) -> None:
    try:
        with open(LOG_PATH, "a") as handle:
            handle.write(f"{datetime.utcnow().isoformat()}Z SessionStart {message}\n")
    except Exception:
        pass


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        log_line("invalid-json")
        sys.exit(0)

    cwd = data.get("cwd", "")
    language = detect_language(cwd)
    project = detect_project(cwd)
    log_line(f"start cwd={cwd!r} language={language!r} project={project!r}")

    cmd = ["vidya", "--json", "brief"]
    if language:
        cmd += ["--language", language]
    if project:
        cmd += ["--project", project]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if proc.returncode != 0 or not proc.stdout.strip():
            log_line(f"vidya-brief-empty returncode={proc.returncode}")
            sys.exit(0)
        brief = json.loads(proc.stdout)
    except Exception:
        log_line("vidya-brief-exception")
        sys.exit(0)

    state = brief.get("project_state", {})
    total = state.get("total_items", 0)
    if total < 3:
        log_line(f"skip-low-total total={total}")
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
            lines.append(f"  - {item['pattern'][:70]}")
            lines.append(f"    {item['reason']}")

    log_line(f"emit total={total} attention={len(attention)}")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "\n".join(lines),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
