from __future__ import annotations

import unittest
from collections import Counter

from wordbyword.dictionary_build.candidates import (
    POS_WEIGHTS,
    CandidateAggregate,
    dispersion_score,
    frequency_rate_per_10k,
    frequency_score_v2,
    frequency_usefulness_score,
    parse_roi_score,
    roi_score,
    roi_score_sort_key,
    term_quality_score,
    term_quality_score_v2,
)


class CandidateScoringTest(unittest.TestCase):
    def test_frequency_usefulness_peaks_then_penalizes_overuse(self) -> None:
        self.assertEqual(frequency_usefulness_score(0, 10_000), 0.0)
        self.assertEqual(frequency_usefulness_score(10, 10_000), 1.0)
        self.assertEqual(frequency_usefulness_score(40, 10_000), 0.0)
        self.assertEqual(frequency_usefulness_score(100, 10_000), -0.5)
        self.assertEqual(frequency_usefulness_score(200, 10_000), -0.5)

    def test_frequency_score_v2_is_symmetric_log_gaussian_hill(self) -> None:
        self.assertEqual(frequency_rate_per_10k(10, 10_000), 10.0)
        self.assertEqual(round(frequency_score_v2(10.0), 3), 0.5)
        self.assertEqual(round(frequency_score_v2(5.6718372568), 3), 0.382)
        self.assertEqual(round(frequency_score_v2(17.1359339777), 3), 0.382)
        self.assertLess(frequency_score_v2(0.0), 0.0)
        self.assertLess(frequency_score_v2(1000.0), 0.0)

    def test_quality_uses_surface_frequency_for_overuse_penalty(self) -> None:
        normal = CandidateAggregate(
            canonical_key="보다",
            display="보다",
            kind="term",
            count=10,
            surface_count=0,
            documents={"book-a", "book-b", "book-c"},
            variants=Counter({"보다": 10, "봅시다": 1}),
            pos_groups=Counter({"verb": 10}),
        )
        overused_surface = CandidateAggregate(
            canonical_key="보다",
            display="보다",
            kind="term",
            count=10,
            surface_count=100,
            documents={"book-a", "book-b", "book-c"},
            variants=Counter({"보다": 10, "봅시다": 100}),
            pos_groups=Counter({"verb": 10}),
        )

        self.assertGreater(
            term_quality_score(normal, total_document_count=3, total_token_occurrences=10_000),
            term_quality_score(overused_surface, total_document_count=3, total_token_occurrences=10_000),
        )

    def test_quality_score_v2_is_added_without_changing_legacy_quality_score(self) -> None:
        aggregate = CandidateAggregate(
            canonical_key="보다",
            display="보다",
            kind="term",
            count=10,
            documents={"book-a", "book-b", "book-c"},
            variants=Counter({"보다": 10, "봅니다": 2}),
            pos_groups=Counter({"verb": 10}),
        )

        self.assertEqual(
            term_quality_score(aggregate, total_document_count=3, total_token_occurrences=10_000),
            0.849,
        )
        quality_v2 = term_quality_score_v2(aggregate, total_document_count=3, total_token_occurrences=10_000)
        self.assertEqual(quality_v2["frequency_rate_per_10k"], 10.0)
        self.assertEqual(quality_v2["frequency_score_v2"], 0.5)
        self.assertEqual(quality_v2["dispersion_score_v2"], 0.5)
        self.assertEqual(quality_v2["pos_score_v2"], 0.22)
        self.assertEqual(quality_v2["quality_score_v2"], 0.349)

    def test_dispersion_uses_all_parsed_documents(self) -> None:
        self.assertAlmostEqual(dispersion_score(1, 3), 1 / 3)
        self.assertAlmostEqual(dispersion_score(2, 3), 2 / 3)
        self.assertEqual(dispersion_score(3, 3), 1.0)

    def test_pos_weights_match_curriculum_priorities(self) -> None:
        self.assertEqual(POS_WEIGHTS["foreign_or_hanja"], 0.86)
        self.assertEqual(POS_WEIGHTS["proper_or_specific_noun"], 0.45)
        self.assertGreater(POS_WEIGHTS["foreign_or_hanja"], POS_WEIGHTS["proper_or_specific_noun"])

    def test_roi_score_is_a_placeholder_until_dynamic_priority_logic_exists(self) -> None:
        self.assertIsNone(roi_score())

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
