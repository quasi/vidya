#!/usr/bin/env python3
"""PostToolUse hook (Bash) — record test_failed feedback when a command fails.

Only fires when:
  1. The bash command exits with a non-zero code.
  2. Vidya has knowledge items that match the failed command's context.

The second condition prevents noise: if Vidya has no relevant knowledge, there's
nothing to decay. We only record feedback when Vidya's items might have contributed
to the failure.
"""

import json
import os
import re
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


def extract_exit_code(tool_response: object) -> int | None:
    """Try to extract exit code from the tool response."""
    # Structured response
    if isinstance(tool_response, dict):
        code = tool_response.get("exit_code") or tool_response.get("exitCode")
        if code is not None:
            return int(code)

    # Text response — look for "Exit code: N" or "exit code N"
    text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response)
    match = re.search(r'[Ee]xit\s*[Cc]ode[:\s]+(\d+)', text)
    if match:
        return int(match.group(1))

    # "Command failed" without explicit code — treat as non-zero
    if re.search(r'[Cc]ommand\s+failed', text):
        return 1

    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")
    cwd = data.get("cwd", "")

    exit_code = extract_exit_code(tool_response)
    if exit_code is None or exit_code == 0:
        sys.exit(0)

    # Get the command that failed
    command = ""
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")
    elif isinstance(tool_input, str):
        try:
            command = json.loads(tool_input).get("command", tool_input)
        except json.JSONDecodeError:
            command = tool_input

    if not command:
        sys.exit(0)

    language = detect_language(cwd)
    project = detect_project(cwd)

    # Only record feedback if Vidya has relevant items — avoid noise
    query_cmd = ["vidya", "query", "--context", command[:300], "--min-confidence", "0.1"]
    if language:
        query_cmd += ["--language", language]
    if project:
        query_cmd += ["--project", project]

    try:
        query_proc = subprocess.run(query_cmd, capture_output=True, text=True, timeout=5)
        query_output = query_proc.stdout.strip()
    except Exception:
        sys.exit(0)

    if not query_output or query_output == "No matching items found.":
        sys.exit(0)

    # Build a concise failure detail
    error_text = str(tool_response)
    # Trim to the first 300 chars of useful error output
    error_snippet = error_text[:300].replace("\n", " ").strip()
    detail = f"Command failed (exit {exit_code}): {command[:200]}"
    if error_snippet:
        detail += f" | Error: {error_snippet}"

    feedback_cmd = [
        "vidya", "feedback",
        "--type", "test_failed",
        "--detail", detail[:400],
    ]
    if language:
        feedback_cmd += ["--language", language]
    if project:
        feedback_cmd += ["--project", project]

    try:
        subprocess.run(feedback_cmd, capture_output=True, text=True, timeout=5)
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
