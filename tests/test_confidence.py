"""Tests for confidence.py — three critical paths from the implementation plan."""

import pytest

from vidya.confidence import (
    FRESHNESS_DECAY_RATE,
    FRESHNESS_FLOOR,
    TRUST_DECAY,
    TRUST_GROWTH,
    compute_freshness,
    effective_confidence,
    update_on_failure,
    update_on_success,
)


# --- Path 1: 20 successes from 0.0 ---

def test_success_increases_confidence():
    item = {"base_confidence": 0.0, "fire_count": 0, "success_count": 0}
    update_on_success(item)
    assert item["base_confidence"] > 0.0


def test_20_successes_approaches_but_never_reaches_1():
    item = {"base_confidence": 0.0, "fire_count": 0, "success_count": 0}
    for _ in range(20):
        update_on_success(item)
    assert item["base_confidence"] > 0.5     # meaningful growth
    assert item["base_confidence"] < 1.0     # asymptotic — never reaches 1


def test_success_increments_counts():
    item = {"base_confidence": 0.3, "fire_count": 5, "success_count": 4}
    update_on_success(item)
    assert item["fire_count"] == 6
    assert item["success_count"] == 5


def test_success_updates_last_fired():
    """Firing an item sets last_fired; compute_freshness(0) will return 1.0."""
    item = {"base_confidence": 0.3, "fire_count": 0, "success_count": 0}
    update_on_success(item)
    assert item.get("last_fired") is not None
    # Freshness is computed dynamically — 0 days since last_fired → 1.0
    assert compute_freshness(0) == pytest.approx(1.0)


# --- Path 2: one failure from 0.64, recovery ---

def test_failure_from_064_drops_to_0448():
    item = {"base_confidence": 0.64, "fire_count": 10, "fail_count": 0}
    update_on_failure(item)
    assert item["base_confidence"] == pytest.approx(0.64 * TRUST_DECAY)


def test_failure_decay_is_trust_decay_factor():
    """base_confidence *= TRUST_DECAY after one failure."""
    item = {"base_confidence": 0.64, "fire_count": 0, "fail_count": 0}
    update_on_failure(item)
    assert item["base_confidence"] == pytest.approx(0.64 * 0.70, rel=1e-6)


def test_recovery_from_failure_takes_about_8_successes():
    """From 0.448 (after failure from 0.64), ~8 successes to recover to >= 0.64."""
    item = {"base_confidence": 0.64, "fire_count": 0, "fail_count": 0, "success_count": 0}
    update_on_failure(item)
    assert item["base_confidence"] < 0.64

    recovered = False
    for i in range(1, 15):
        update_on_success(item)
        if item["base_confidence"] >= 0.64:
            assert i <= 10, f"Recovery took {i} successes, expected ~8"
            recovered = True
            break
    assert recovered, "Never recovered to 0.64"


def test_failure_increments_fail_count():
    item = {"base_confidence": 0.5, "fire_count": 3, "fail_count": 1}
    update_on_failure(item)
    assert item["fail_count"] == 2
    assert item["fire_count"] == 4


def test_failure_updates_last_fired():
    """Failure also sets last_fired — item was recently tested."""
    item = {"base_confidence": 0.5, "fire_count": 0, "fail_count": 0}
    update_on_failure(item)
    assert item.get("last_fired") is not None
    # Freshness is computed dynamically — 0 days since last_fired → 1.0
    assert compute_freshness(0) == pytest.approx(1.0)


# --- Path 3: freshness decay over time ---

def test_freshness_is_1_for_0_days():
    assert compute_freshness(days_since_reference=0) == pytest.approx(1.0)


def test_freshness_decay_after_30_days():
    f = compute_freshness(days_since_reference=30)
    expected = max(FRESHNESS_FLOOR, 1.0 - FRESHNESS_DECAY_RATE * 30)
    assert f == pytest.approx(expected)


def test_freshness_decay_after_90_days():
    f = compute_freshness(days_since_reference=90)
    expected = max(FRESHNESS_FLOOR, 1.0 - FRESHNESS_DECAY_RATE * 90)
    assert f == pytest.approx(expected)


def test_freshness_floor_at_140_days():
    """140 days = 0.005 * 140 = 0.7 decay → 1.0 - 0.7 = 0.3 = FRESHNESS_FLOOR."""
    f = compute_freshness(days_since_reference=140)
    assert f == pytest.approx(FRESHNESS_FLOOR)


def test_freshness_stays_at_floor_beyond_140_days():
    f200 = compute_freshness(days_since_reference=200)
    assert f200 == pytest.approx(FRESHNESS_FLOOR)


def test_freshness_none_returns_floor():
    """Item never fired: freshness = FRESHNESS_FLOOR."""
    assert compute_freshness(days_since_reference=None) == pytest.approx(FRESHNESS_FLOOR)


# --- effective_confidence ---

def test_effective_confidence_is_product():
    assert effective_confidence(0.8, 0.5) == pytest.approx(0.4)


def test_effective_confidence_zero_base():
    assert effective_confidence(0.0, 1.0) == pytest.approx(0.0)


def test_effective_confidence_full_freshness():
    assert effective_confidence(0.6, 1.0) == pytest.approx(0.6)
