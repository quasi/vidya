#!/usr/bin/env python3
"""Codex PostToolUse hook — record test_failed feedback when Bash fails."""

import json
import os
import re
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


def extract_exit_code(tool_response: object) -> Optional[int]:
    if isinstance(tool_response, dict):
        code = tool_response.get("exit_code") or tool_response.get("exitCode")
        if code is not None:
            return int(code)

    text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response)
    match = re.search(r"[Ee]xit\s*[Cc]ode[:\s]+(\d+)", text)
    if match:
        return int(match.group(1))
    if re.search(r"[Cc]ommand\s+failed", text):
        return 1
    return None


def log_line(message: str) -> None:
    try:
        with open(LOG_PATH, "a") as handle:
            handle.write(f"{datetime.utcnow().isoformat()}Z PostToolUse {message}\n")
    except Exception:
        pass


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        log_line("invalid-json")
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        log_line(f"skip-tool tool={data.get('tool_name')!r}")
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")
    cwd = data.get("cwd", "")

    exit_code = extract_exit_code(tool_response)
    if exit_code is None or exit_code == 0:
        log_line(f"skip-exit exit_code={exit_code!r}")
        sys.exit(0)

    command = ""
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")
    elif isinstance(tool_input, str):
        command = tool_input

    if not command:
        log_line("skip-empty-command")
        sys.exit(0)

    language = detect_language(cwd)
    project = detect_project(cwd)
    log_line(f"start exit_code={exit_code} language={language!r} project={project!r}")

    query_cmd = ["vidya", "query", "--context", command[:300], "--min-confidence", "0.1"]
    if language:
        query_cmd += ["--language", language]
    if project:
        query_cmd += ["--project", project]

    try:
        query_proc = subprocess.run(query_cmd, capture_output=True, text=True, timeout=5)
        query_output = query_proc.stdout.strip()
    except Exception:
        log_line("vidya-query-exception")
        sys.exit(0)

    if not query_output or query_output == "No matching items found.":
        log_line("skip-no-results")
        sys.exit(0)

    error_text = str(tool_response)
    error_snippet = error_text[:300].replace("\n", " ").strip()
    detail = f"Command failed (exit {exit_code}): {command[:200]}"
    if error_snippet:
        detail += f" | Error: {error_snippet}"

    feedback_cmd = [
        "vidya",
        "feedback",
        "--type",
        "test_failed",
        "--detail",
        detail[:400],
    ]
    if language:
        feedback_cmd += ["--language", language]
    if project:
        feedback_cmd += ["--project", project]

    try:
        subprocess.run(feedback_cmd, capture_output=True, text=True, timeout=5)
    except Exception:
        log_line("feedback-exception")
        pass

    log_line("emit-feedback")

    sys.exit(0)


if __name__ == "__main__":
    main()
