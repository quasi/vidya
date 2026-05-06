#!/usr/bin/env python3
"""Codex UserPromptSubmit hook — query Vidya and inject task-relevant knowledge."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Optional

LOG_PATH = "/Users/quasi/.codex/hooks/vidya-hooks.log"
_ACK_PHRASES = {
    "ok",
    "okay",
    "yes",
    "yep",
    "sure",
    "continue",
    "go ahead",
    "build it",
    "do it",
    "sounds good",
    "all good",
    "agree",
}


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
            handle.write(f"{datetime.utcnow().isoformat()}Z UserPromptSubmit {message}\n")
    except Exception:
        pass


def normalize_prompt(prompt: str) -> str:
    lowered = prompt.lower().strip()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return " ".join(lowered.split())


def is_low_signal_prompt(prompt: str) -> bool:
    normalized = normalize_prompt(prompt)
    if not normalized:
        return True
    if normalized in _ACK_PHRASES:
        return True
    words = normalized.split()
    return len(words) <= 3 and normalized in _ACK_PHRASES


def summarize_item(item: dict) -> str:
    guidance = " ".join(item.get("guidance", "").strip().split())
    pattern = " ".join(item.get("pattern", "").strip().split())
    summary = guidance or pattern
    if ". " in summary:
        summary = summary.split(". ", 1)[0].strip()
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "..."
    item_type = item.get("type", "?")
    return f"- {item_type}: {summary}"


def is_preferred_item(item: dict, project: Optional[str]) -> bool:
    match_reason = item.get("match_reason", "")
    if "scope=global" in match_reason:
        return True
    if project and f"project={project}" in match_reason:
        return True
    return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        log_line("invalid-json")
        sys.exit(0)

    prompt = data.get("prompt", "").strip()
    cwd = data.get("cwd", "")
    if len(prompt) < 20 or prompt.startswith("/") or is_low_signal_prompt(prompt):
        log_line(f"skip-low-signal prompt_len={len(prompt)}")
        sys.exit(0)

    language = detect_language(cwd)
    project = detect_project(cwd)
    log_line(f"start prompt_len={len(prompt)} language={language!r} project={project!r}")

    cmd = ["vidya", "--json", "query", "--context", prompt[:400], "--min-confidence", "0.3"]
    if language:
        cmd += ["--language", language]
    if project:
        cmd += ["--project", project]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = proc.stdout.strip()
        payload = json.loads(output) if output else {}
    except Exception:
        log_line("vidya-query-exception")
        sys.exit(0)

    items = payload.get("items", [])
    if not items:
        log_line("skip-no-results")
        sys.exit(0)

    preferred_items = [item for item in items if is_preferred_item(item, project)]
    if not preferred_items:
        log_line("skip-no-preferred-results")
        sys.exit(0)

    lines = ["[Vidya knowledge for this task]"]
    for item in preferred_items[:3]:
        lines.append(summarize_item(item))

    log_line(f"emit count={min(len(preferred_items), 3)}")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "\n".join(lines),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
