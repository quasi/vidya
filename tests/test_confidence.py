"""Tests for confidence.py — heuristic update paths and source confidence tiers."""

import pytest

from vidya.confidence import (
    SOURCE_CONFIDENCE,
    TRUST_DECAY,
    TRUST_GROWTH,
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
    """Firing an item sets last_fired."""
    item = {"base_confidence": 0.3, "fire_count": 0, "success_count": 0}
    update_on_success(item)
    assert item.get("last_fired") is not None


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


# --- Path 3: SOURCE_CONFIDENCE tiers ---

def test_source_confidence_user_correction():
    assert SOURCE_CONFIDENCE["user_correction"] == 0.85


def test_source_confidence_user_confirmation():
    assert SOURCE_CONFIDENCE["user_confirmation"] == 0.70


def test_source_confidence_review_rejected():
    assert SOURCE_CONFIDENCE["review_rejected"] == 0.65


def test_source_confidence_test_outcome():
    assert SOURCE_CONFIDENCE["test_outcome"] == 0.60


def test_source_confidence_seed():
    assert SOURCE_CONFIDENCE["seed"] == 0.60


def test_source_confidence_extraction():
    assert SOURCE_CONFIDENCE["extraction"] == 0.40


def test_source_confidence_unknown_raises():
    with pytest.raises(KeyError):
        SOURCE_CONFIDENCE["unknown_source"]
