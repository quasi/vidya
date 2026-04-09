"""Tests for new/modified guidance functions: for_feedback decomposition,
for_query bundled results, and for_evolve."""

import pytest

from vidya.guidance import for_feedback, for_query, for_evolve


class TestForFeedbackDecomposed:

    def test_decomposed_bundle_returns_decompose_note(self, db):
        learning = {"decomposed": True, "source_ids": ["id1", "id2", "id3"]}
        result = for_feedback("user_correction", learning, db)
        assert "Bundle decomposed" in result["note"]
        assert "3 source items" in result["note"]
        assert "source item" in result["next_step"]

    def test_decomposed_single_source(self, db):
        learning = {"decomposed": True, "source_ids": ["id1"]}
        result = for_feedback("user_correction", learning, db)
        assert "1 source item" in result["note"]

    def test_decomposed_no_source_ids(self, db):
        learning = {"decomposed": True}
        result = for_feedback("user_correction", learning, db)
        assert "Bundle decomposed" in result["note"]
        assert "0 source items" in result["note"]

    def test_decomposed_check_happens_before_other_logic(self, db):
        # Even for a feedback type that normally returns a different note
        learning = {"decomposed": True, "source_ids": ["a", "b"]}
        result = for_feedback("user_confirmation", learning, db)
        assert "Bundle decomposed" in result["note"]

    def test_non_decomposed_learning_follows_normal_path(self, db):
        learning = {"merged": True, "item_id": "abc123"}
        result = for_feedback("user_correction", learning, db)
        assert "Merged" in result["note"]

    def test_none_learning_follows_normal_path(self, db):
        result = for_feedback("user_correction", None, db)
        assert "No new item created" in result["note"]


class TestForQueryBundled:

    def test_no_items_returns_empty_note(self, db):
        result = for_query([], "test", db)
        assert "No items matched" in result["note"]

    def test_bundled_items_mentioned_in_high_confidence_result(self, db):
        items = [
            {"effective_confidence": 0.8, "type": "convention", "match_source": "bundle"},
            {"effective_confidence": 0.7, "type": "convention", "match_source": "direct"},
        ]
        result = for_query(items, "test", db)
        assert "compacted from bundles" in result["note"]
        assert "1 result" in result["note"]

    def test_bundled_items_mentioned_in_medium_confidence_result(self, db):
        items = [
            {"effective_confidence": 0.3, "type": "convention", "match_source": "bundle"},
            {"effective_confidence": 0.4, "type": "convention", "match_source": "bundle"},
        ]
        result = for_query(items, "test", db)
        assert "compacted from bundles" in result["note"]
        assert "2 results" in result["note"]

    def test_no_bundled_items_no_mention(self, db):
        items = [
            {"effective_confidence": 0.8, "type": "convention", "match_source": "direct"},
        ]
        result = for_query(items, "test", db)
        assert "bundle" not in result["note"].lower()

    def test_bundled_singular_vs_plural(self, db):
        items_single = [{"effective_confidence": 0.3, "type": "convention", "match_source": "bundle"}]
        items_plural = [
            {"effective_confidence": 0.3, "type": "convention", "match_source": "bundle"},
            {"effective_confidence": 0.4, "type": "convention", "match_source": "bundle"},
        ]
        single = for_query(items_single, "test", db)
        plural = for_query(items_plural, "test", db)
        assert "1 result compacted" in single["note"]
        assert "2 results compacted" in plural["note"]


class TestForEvolve:

    def test_no_clusters_found(self, db):
        result = for_evolve(0, 0, 0, db)
        assert "No clusters found" in result["note"]
        assert "too small" in result["note"] or "diverse" in result["note"]

    def test_candidates_created(self, db):
        result = for_evolve(3, 2, 0, db)
        assert "2 bundle candidates" in result["note"]
        assert "3 clusters" in result["note"]
        assert "--review" in result["next_step"]

    def test_candidates_created_singular(self, db):
        result = for_evolve(1, 1, 0, db)
        assert "1 bundle candidate" in result["note"]
        assert "1 cluster" in result["note"]

    def test_pending_review_no_new_candidates(self, db):
        result = for_evolve(2, 0, 5, db)
        assert "5 candidates pending review" in result["note"]
        assert "--review" in result["next_step"]

    def test_pending_review_singular(self, db):
        result = for_evolve(1, 0, 1, db)
        assert "1 candidate pending review" in result["note"]

    def test_clusters_found_but_nothing_to_do(self, db):
        result = for_evolve(3, 0, 0, db)
        assert "3 clusters" in result["note"]
        assert "No new candidates" in result["note"]
