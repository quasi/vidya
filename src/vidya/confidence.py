"""Confidence model: heuristic updating by source and outcome.

Two-field model per design:
  base_confidence — epistemic trust, initialised from SOURCE_CONFIDENCE at creation
                    and updated by heuristic formula on outcomes
  source          — the event type that created or last confirmed the item

SOURCE_CONFIDENCE maps source types to their initial base_confidence values.
Outcomes (success/failure) are applied via update_on_success / update_on_failure.
"""

from datetime import datetime, timezone
from typing import Any

TRUST_GROWTH: float = 0.05   # alpha — slow to trust
TRUST_DECAY: float = 0.70    # beta — quick to doubt

SOURCE_CONFIDENCE: dict[str, float] = {
    "user_correction": 0.85,
    "user_confirmation": 0.70,
    "review_rejected": 0.65,
    "test_outcome": 0.60,
    "seed": 0.60,
    "extraction": 0.40,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_on_success(item: dict[str, Any]) -> None:
    """Heuristic update after a successful observation. Mutates item dict.

    Sets last_fired to now.
    """
    item["base_confidence"] = (
        item["base_confidence"] + TRUST_GROWTH * (1.0 - item["base_confidence"])
    )
    item["last_fired"] = _now()
    item["fire_count"] = item.get("fire_count", 0) + 1
    item["success_count"] = item.get("success_count", 0) + 1


def update_on_failure(item: dict[str, Any]) -> None:
    """Heuristic update after a failed observation. Mutates item dict.

    Sets last_fired to now. Item was recently tested.
    """
    item["base_confidence"] = item["base_confidence"] * TRUST_DECAY
    item["last_fired"] = _now()
    item["fire_count"] = item.get("fire_count", 0) + 1
    item["fail_count"] = item.get("fail_count", 0) + 1
