#!/usr/bin/env python3
"""UserPromptSubmit hook — query Vidya and inject relevant knowledge as context.

Reads the user prompt from stdin (Claude Code hook JSON), detects language from
the project directory, runs `vidya query`, and returns the results as
additionalContext so Claude sees them before responding.
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

    prompt = data.get("prompt", "").strip()
    cwd = data.get("cwd", "")

    # Skip very short prompts, pure commands, and skill invocations
    if len(prompt) < 15 or prompt.startswith("/"):
        sys.exit(0)

    language = detect_language(cwd)
    project = detect_project(cwd)

    cmd = ["vidya", "query", "--context", prompt[:400], "--min-confidence", "0.1"]
    if language:
        cmd += ["--language", language]
    if project:
        cmd += ["--project", project]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = proc.stdout.strip()
    except Exception:
        sys.exit(0)

    if not output or output == "No matching items found.":
        sys.exit(0)

    response = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"[Vidya knowledge for this task]\n{output}",
        }
    }
    print(json.dumps(response))


if __name__ == "__main__":
    main()
