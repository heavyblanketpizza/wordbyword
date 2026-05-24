from __future__ import annotations

import unittest
from collections import Counter

from wordbyword.dictionary_build.candidates import (
    POS_WEIGHTS,
    CandidateAggregate,
    dispersion_score,
    frequency_rate_per_10k,
    frequency_signal_score,
    normalized_learning_signal_score,
    parse_roi_score,
    roi_score_sort_key,
    row_learning_signal_score,
    term_learning_signal_scores,
)


class CandidateScoringTest(unittest.TestCase):
    def test_frequency_signal_score_is_symmetric_log_gaussian_hill(self) -> None:
        self.assertEqual(frequency_rate_per_10k(10, 10_000), 10.0)
        self.assertEqual(round(frequency_signal_score(10.0), 3), 0.5)
        self.assertEqual(round(frequency_signal_score(5.6718372568), 3), 0.382)
        self.assertEqual(round(frequency_signal_score(17.1359339777), 3), 0.382)
        self.assertLess(frequency_signal_score(0.0), 0.0)
        self.assertLess(frequency_signal_score(1000.0), 0.0)

    def test_learning_signal_score_keeps_centered_formula(self) -> None:
        aggregate = CandidateAggregate(
            canonical_key="보다",
            display="보다",
            kind="term",
            count=10,
            documents={"book-a", "book-b", "book-c"},
            variants=Counter({"보다": 10, "봅니다": 2}),
            pos_groups=Counter({"verb": 10}),
        )

        scores = term_learning_signal_scores(
            aggregate,
            total_document_count=3,
            total_token_occurrences=10_000,
        )

        self.assertEqual(scores["frequency_rate_per_10k"], 10.0)
        self.assertEqual(scores["frequency_signal_score"], 0.5)
        self.assertEqual(scores["dispersion_signal_score"], 0.5)
        self.assertEqual(scores["pos_signal_score"], 0.22)
        self.assertEqual(scores["learning_signal_score"], 0.349)
        self.assertNotIn("quality_score_v2", scores)

    def test_normalized_learning_signal_score_maps_to_roi_range(self) -> None:
        self.assertEqual(normalized_learning_signal_score({"learning_signal_score": -0.5}), 0.0)
        self.assertEqual(normalized_learning_signal_score({"learning_signal_score": 0.0}), 0.5)
        self.assertEqual(normalized_learning_signal_score({"learning_signal_score": 0.349}), 0.849)
        self.assertEqual(normalized_learning_signal_score({"learning_signal_score": 0.5}), 1.0)

    def test_learning_signal_score_reads_legacy_fields_during_migration(self) -> None:
        self.assertEqual(row_learning_signal_score({"learning_signal_score": 0.2}), 0.2)
        self.assertEqual(row_learning_signal_score({"quality_score_v2": 0.2}), 0.2)
        self.assertEqual(row_learning_signal_score({"quality_score": 0.8}), 0.3)
        self.assertEqual(normalized_learning_signal_score({"quality_score": 0.8}), 0.8)

    def test_dispersion_uses_all_parsed_documents(self) -> None:
        self.assertAlmostEqual(dispersion_score(1, 3), 1 / 3)
        self.assertAlmostEqual(dispersion_score(2, 3), 2 / 3)
        self.assertEqual(dispersion_score(3, 3), 1.0)

    def test_pos_weights_match_curriculum_priorities(self) -> None:
        self.assertEqual(POS_WEIGHTS["foreign_or_hanja"], 0.86)
        self.assertEqual(POS_WEIGHTS["proper_or_specific_noun"], 0.45)
        self.assertGreater(POS_WEIGHTS["foreign_or_hanja"], POS_WEIGHTS["proper_or_specific_noun"])

    def test_parse_roi_score_accepts_uncalculated_and_calculated_scores(self) -> None:
        self.assertIsNone(parse_roi_score(None))
        self.assertIsNone(parse_roi_score(""))
        self.assertIsNone(parse_roi_score("   "))
        self.assertEqual(parse_roi_score(0.0), 0.0)
        self.assertEqual(parse_roi_score("0.5"), 0.5)
        self.assertEqual(parse_roi_score(1.0), 1.0)

    def test_parse_roi_score_rejects_out_of_range_scores(self) -> None:
        for value in (-1.0, "-0.01", 1.01, True, "not-a-score"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_roi_score(value)

    def test_roi_score_sort_key_puts_uncalculated_scores_last(self) -> None:
        rows = [None, 0.0, 0.8, 0.2, ""]

        self.assertEqual(sorted(rows, key=roi_score_sort_key), [0.8, 0.2, 0.0, None, ""])


if __name__ == "__main__":
    unittest.main()
