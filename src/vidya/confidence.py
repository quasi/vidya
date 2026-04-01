"""Confidence model: Bayesian updating and freshness decay.

Two-field model per design:
  base_confidence — epistemic trust, updated by Bayesian formula on outcomes
  freshness       — temporal staleness, computed at query time from last_fired
  effective_confidence = base_confidence * freshness  (computed, never stored)

Freshness is NOT stored in the database. It is computed on demand via
compute_freshness(days_since_fired). Firing an item updates last_fired to now,
which causes compute_freshness to return 1.0 until time passes again.
"""

from datetime import datetime, timezone
from typing import Any

TRUST_GROWTH: float = 0.05   # alpha — slow to trust
TRUST_DECAY: float = 0.70    # beta — quick to doubt
FRESHNESS_DECAY_RATE: float = 0.005   # 0.5% per day
FRESHNESS_FLOOR: float = 0.3          # never fully forgotten


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_on_success(item: dict[str, Any]) -> None:
    """Bayesian update after a successful observation. Mutates item dict.

    Sets last_fired to now. Freshness resets implicitly: compute_freshness(0) == 1.0.
    """
    item["base_confidence"] = (
        item["base_confidence"] + TRUST_GROWTH * (1.0 - item["base_confidence"])
    )
    item["last_fired"] = _now()
    item["fire_count"] = item.get("fire_count", 0) + 1
    item["success_count"] = item.get("success_count", 0) + 1


def update_on_failure(item: dict[str, Any]) -> None:
    """Bayesian update after a failed observation. Mutates item dict.

    Sets last_fired to now. Item was recently tested — freshness resets implicitly.
    """
    item["base_confidence"] = item["base_confidence"] * TRUST_DECAY
    item["last_fired"] = _now()
    item["fire_count"] = item.get("fire_count", 0) + 1
    item["fail_count"] = item.get("fail_count", 0) + 1


def compute_freshness(days_since_reference: int | None) -> float:
    """Compute freshness from days elapsed since a reference timestamp.

    Callers should pass days since last_fired (preferred) or first_seen (fallback).
    Use days_since_reference() to compute this from raw timestamps.

    None means no reference timestamp exists — returns FRESHNESS_FLOOR.
    This should be rare; all items have first_seen set.
    """
    if days_since_reference is None:
        return FRESHNESS_FLOOR
    return max(FRESHNESS_FLOOR, 1.0 - FRESHNESS_DECAY_RATE * days_since_reference)


def days_since_reference(
    last_fired: str | None,
    first_seen: str | None,
    now: datetime,
) -> int | None:
    """Compute days since the reference timestamp for freshness.

    Uses last_fired if available, falls back to first_seen so newly
    created items (last_fired=NULL) start fresh rather than stale.
    Returns None only if both are NULL (should not happen for real items).
    """
    ref_ts = last_fired or first_seen
    if ref_ts is None:
        return None
    last = datetime.fromisoformat(ref_ts)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0, (now - last).days)


def effective_confidence(base_confidence: float, freshness: float) -> float:
    """Combined ranking score. Computed at query time, never stored."""
    return base_confidence * freshness
