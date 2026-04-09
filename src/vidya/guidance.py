"""Contextual guidance for JSON CLI responses.

Tier 1: rule-based on response data (counts, flags, obvious next actions).
Tier 2: template-based combining data from multiple tables.

Each function returns {"note": str, "next_step": str}.
"""

import sqlite3
from typing import Any


def for_start_task(
    knowledge: list[dict[str, Any]],
    project: str | None,
    db: sqlite3.Connection,
) -> dict[str, str]:
    if not knowledge:
        note = "No items matched this task."
        if project:
            note += f" Check if a seed file exists for project '{project}'."
        else:
            note += " Try adding --project or widening context."
        return {"note": note, "next_step": "Use vidya seed to populate knowledge, or vidya query with broader terms."}

    high = [k for k in knowledge if k.get("effective_confidence", 0) > 0.5]
    medium = [k for k in knowledge if 0.2 <= k.get("effective_confidence", 0) <= 0.5]
    unfired = [k for k in knowledge if k.get("fire_count", 0) == 0]

    parts = []
    if high:
        parts.append(f"{len(high)} HIGH-confidence item{'s' if len(high) != 1 else ''} returned.")
    if medium:
        parts.append(f"{len(medium)} MEDIUM (provisional).")
    if unfired:
        parts.append(f"{len(unfired)} never validated — verify before relying on them.")

    note = " ".join(parts)

    if high:
        preconditions = [k for k in high if k.get("type") == "precondition"]
        if preconditions:
            next_step = f"Check {len(preconditions)} HIGH-confidence precondition{'s' if len(preconditions) != 1 else ''} before starting work."
        else:
            next_step = "Follow HIGH-confidence items. Call vidya feedback after corrections or confirmations."
    else:
        next_step = "All items are MEDIUM — treat as suggestions, not rules. Confirm with vidya feedback if they help."

    return {"note": note, "next_step": next_step}


def for_end_task(
    outcome: str,
    task_id: str,
    db: sqlite3.Connection,
) -> dict[str, str]:
    task = db.execute(
        "SELECT language, project FROM task_records WHERE id = ?", (task_id,)
    ).fetchone()
    if task:
        recent = db.execute(
            "SELECT outcome FROM task_records WHERE language IS ? AND project IS ? "
            "ORDER BY timestamp_start DESC LIMIT 5",
            (task["language"], task["project"]),
        ).fetchall()
        recent_failures = sum(1 for r in recent if r["outcome"] == "failure")
    else:
        recent_failures = 0

    if outcome == "failure":
        note = "Task ended in failure."
        if recent_failures >= 3:
            note += f" {recent_failures} of the last 5 tasks in this scope failed — review knowledge items."
        return {
            "note": note,
            "next_step": "Record what went wrong with vidya feedback --type user_correction so Vidya learns from this failure.",
        }

    if outcome == "success":
        return {
            "note": "Task completed successfully.",
            "next_step": "If Vidya's knowledge helped, confirm with vidya feedback --type user_confirmation to strengthen those items.",
        }

    return {
        "note": f"Task ended with outcome '{outcome}'.",
        "next_step": "Record any corrections or lessons via vidya feedback before moving on.",
    }


def for_feedback(
    feedback_type: str,
    learning: dict[str, Any] | None,
    db: sqlite3.Connection,
) -> dict[str, str]:
    if learning and learning.get("decomposed"):
        source_count = len(learning.get("source_ids", []))
        return {
            "note": f"Bundle decomposed. {source_count} source items released for individual review.",
            "next_step": "Re-run vidya feedback targeting the specific source item that needs correction.",
        }

    if feedback_type in ("user_correction", "review_rejected"):
        if learning is None:
            return {
                "note": "Feedback recorded. No new item created (no matching scope).",
                "next_step": "Provide --language and --project scope with feedback for better extraction.",
            }
        if learning.get("merged"):
            return {
                "note": f"Merged into existing item {learning['item_id']}. Evidence strengthened.",
                "next_step": "The existing item's confidence was boosted. No further action needed.",
            }
        return {
            "note": "New item created at confidence 0.15. It needs ~8 confirmations to reach HIGH.",
            "next_step": "Confirm this item works with vidya feedback --type user_confirmation after successful use.",
        }

    if feedback_type in ("user_confirmation", "review_accepted"):
        return {
            "note": "Confidence boosted on matching items.",
            "next_step": "Continue working. Repeated confirmations steadily raise item confidence.",
        }

    if feedback_type == "test_failed":
        return {
            "note": "Confidence decayed on matching items (multiplied by 0.70).",
            "next_step": "If the failure reveals a wrong rule, use vidya feedback --type user_correction to create the right one.",
        }

    return {
        "note": "Feedback recorded.",
        "next_step": "Continue working.",
    }


def for_query(
    items: list[dict[str, Any]],
    context: str,
    db: sqlite3.Connection,
) -> dict[str, str]:
    if not items:
        return {
            "note": "No items matched. FTS tokenizes on individual words — try specific terms, not sentences.",
            "next_step": "Widen context or use different keywords. 'error handling pytest' matches better than 'I need to fix the test errors'.",
        }

    bundled = [i for i in items if i.get("match_source") == "bundle"]

    high = [i for i in items if i.get("effective_confidence", 0) > 0.5]
    medium = [i for i in items if 0.2 <= i.get("effective_confidence", 0) <= 0.5]

    if high:
        preconditions = [i for i in high if i.get("type") == "precondition"]
        anti_patterns = [i for i in high if i.get("type") == "anti_pattern"]
        parts = [f"{len(high)} HIGH-confidence item{'s' if len(high) != 1 else ''}."]
        if preconditions:
            parts.append(f"{len(preconditions)} precondition{'s' if len(preconditions) != 1 else ''} — check before proceeding.")
        if anti_patterns:
            parts.append(f"{len(anti_patterns)} anti-pattern{'s' if len(anti_patterns) != 1 else ''} — avoid these.")
        if bundled:
            parts.append(f"{len(bundled)} result{'s' if len(bundled) != 1 else ''} compacted from bundles (multiple items synthesized into one).")
        return {
            "note": " ".join(parts),
            "next_step": "Follow HIGH items. Call vidya feedback to confirm or correct.",
        }

    note = f"{len(medium)} MEDIUM-confidence item{'s' if len(medium) != 1 else ''} — treat as suggestions, not rules."
    if bundled:
        note += f" {len(bundled)} result{'s' if len(bundled) != 1 else ''} compacted from bundles (multiple items synthesized into one)."
    return {
        "note": note,
        "next_step": "Verify these items apply to your situation. Confirm with vidya feedback if they help.",
    }


def for_explain(
    item: dict[str, Any],
    overridden_by: list[dict[str, Any]],
    db: sqlite3.Connection,
) -> dict[str, str]:
    parts = []
    fire_count = item.get("fire_count", 0)
    fail_count = item.get("fail_count", 0)
    success_count = item.get("success_count", 0)
    source = item.get("source", "unknown")

    if fire_count == 0:
        parts.append(f"This {source}-sourced item has never been validated in practice.")
    if fire_count > 0 and fail_count > success_count:
        fail_rate = round(100 * fail_count / fire_count)
        parts.append(f"Unreliable: {fail_rate}% failure rate ({fail_count}/{fire_count} fires).")
    if overridden_by:
        names = ", ".join(f"'{o['pattern']}'" for o in overridden_by[:3])
        parts.append(f"Overridden by: {names}. This item is suppressed in query results.")
    if not parts:
        conf = item.get("base_confidence", 0)
        parts.append(f"Healthy item: {fire_count} fires, {success_count} successes, confidence {conf:.2f}.")

    if fire_count == 0:
        next_step = "Use vidya feedback to confirm or correct this item after applying it."
    elif overridden_by:
        next_step = "Consider archiving this item if the override is correct."
    else:
        next_step = "No action needed."

    return {"note": " ".join(parts), "next_step": next_step}


def for_stats(
    stats: dict[str, Any],
    db: sqlite3.Connection,
) -> dict[str, str]:
    total = stats.get("total_items", 0)

    if total == 0:
        return {
            "note": "Knowledge base is empty. Seed it before Vidya can help.",
            "next_step": "Use vidya seed --file <rules.md> --language <lang> --project <project> to populate.",
        }

    by_conf = stats.get("by_confidence", {})
    low = by_conf.get("low", 0)
    high = by_conf.get("high", 0)

    if low > total * 0.5:
        return {
            "note": f"{low}/{total} items are LOW confidence — most knowledge is stale or unvalidated.",
            "next_step": "Run vidya items --min-confidence 0 to review LOW items. Confirm or archive them.",
        }

    feedback_count = stats.get("total_feedback", 0)
    task_count = stats.get("total_tasks", 0)

    parts = [f"{total} items ({high} HIGH)."]
    if task_count > 0 and feedback_count == 0:
        parts.append(f"{task_count} tasks recorded but zero feedback — Vidya isn't learning.")
    elif feedback_count > 0:
        parts.append(f"{feedback_count} feedback events have shaped the knowledge base.")

    candidates = stats.get("total_candidates", 0)
    if candidates > 0:
        parts.append(f"{candidates} extraction candidate{'s' if candidates != 1 else ''} pending review.")

    return {
        "note": " ".join(parts),
        "next_step": "Continue using vidya feedback after corrections to keep items accurate.",
    }


def for_maintain(
    health: str,
    stale_count: int,
    archive_result: dict[str, Any] | None,
) -> dict[str, str]:
    if health == "empty":
        return {
            "note": "Knowledge base is empty.",
            "next_step": "Seed knowledge with vidya seed before running maintenance.",
        }

    parts = [f"Health: {health}."]
    if stale_count > 0:
        parts.append(f"{stale_count} stale item{'s' if stale_count != 1 else ''} found.")

    if archive_result:
        if archive_result.get("archived_count", 0) > 0:
            parts.append(f"Archived {archive_result['archived_count']} item(s).")
        elif archive_result.get("would_archive_count", 0) > 0:
            parts.append(f"{archive_result['would_archive_count']} item(s) would be archived. Use --confirm to execute.")

    if health == "degraded":
        next_step = "Review stale items with vidya items --min-confidence 0. Confirm or archive them."
    elif stale_count > 0:
        next_step = "Run vidya maintain --archive --confirm to clean up, or review items individually."
    else:
        next_step = "No maintenance needed. Continue working."

    return {"note": " ".join(parts), "next_step": next_step}


def for_record_step(
    outcome: str,
    matched_items: list[dict[str, Any]],
    db: sqlite3.Connection,
) -> dict[str, str]:
    if outcome == "error":
        note = "Step resulted in error."
        if matched_items:
            note += f" {len(matched_items)} existing item{'s' if len(matched_items) != 1 else ''} matched — check if they cover this scenario."
        return {
            "note": note,
            "next_step": "If this error reveals a pattern, record it with vidya feedback --type user_correction.",
        }

    if matched_items:
        return {
            "note": f"Step recorded. {len(matched_items)} knowledge item{'s' if len(matched_items) != 1 else ''} matched this action.",
            "next_step": "Review matched items — they may contain relevant guidance for next steps.",
        }

    return {
        "note": "Step recorded. No existing knowledge matched this action.",
        "next_step": "If this step represents a reusable pattern, consider recording it via vidya feedback.",
    }


def for_evolve(
    clusters_found: int,
    candidates_created: int,
    pending_review: int,
    db: sqlite3.Connection,
) -> dict[str, str]:
    if clusters_found == 0:
        return {
            "note": "No clusters found. Knowledge base may be too small or items too diverse.",
            "next_step": "Add more knowledge items or feedback events before running evolve.",
        }

    if candidates_created > 0:
        return {
            "note": f"{candidates_created} bundle candidate{'s' if candidates_created != 1 else ''} created from {clusters_found} cluster{'s' if clusters_found != 1 else ''}.",
            "next_step": "Run vidya evolve --review to approve, edit, or reject candidates.",
        }

    if pending_review > 0:
        return {
            "note": f"{pending_review} candidate{'s' if pending_review != 1 else ''} pending review from previous runs.",
            "next_step": "Run vidya evolve --review to approve, edit, or reject candidates.",
        }

    return {
        "note": f"{clusters_found} cluster{'s' if clusters_found != 1 else ''} found. No new candidates created.",
        "next_step": "Run vidya evolve --review if candidates remain, or add more feedback to trigger new clusters.",
    }
