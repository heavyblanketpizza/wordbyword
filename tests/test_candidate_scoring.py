from __future__ import annotations

import unittest
from collections import Counter

from wordbyword.candidates import (
    POS_WEIGHTS,
    CandidateAggregate,
    dispersion_score,
    frequency_usefulness_score,
    roi_score,
    term_quality_score,
)


class CandidateScoringTest(unittest.TestCase):
    def test_frequency_usefulness_peaks_then_penalizes_overuse(self) -> None:
        self.assertEqual(frequency_usefulness_score(0, 10_000), 0.0)
        self.assertEqual(frequency_usefulness_score(10, 10_000), 1.0)
        self.assertEqual(frequency_usefulness_score(40, 10_000), 0.0)
        self.assertEqual(frequency_usefulness_score(100, 10_000), -0.5)
        self.assertEqual(frequency_usefulness_score(200, 10_000), -0.5)

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

    def test_dispersion_uses_all_parsed_documents(self) -> None:
        self.assertAlmostEqual(dispersion_score(1, 3), 1 / 3)
        self.assertAlmostEqual(dispersion_score(2, 3), 2 / 3)
        self.assertEqual(dispersion_score(3, 3), 1.0)

    def test_pos_weights_match_curriculum_priorities(self) -> None:
        self.assertEqual(POS_WEIGHTS["foreign_or_hanja"], 0.86)
        self.assertEqual(POS_WEIGHTS["proper_or_specific_noun"], 0.45)
        self.assertGreater(POS_WEIGHTS["foreign_or_hanja"], POS_WEIGHTS["proper_or_specific_noun"])

    def test_roi_score_is_a_placeholder_until_dynamic_priority_logic_exists(self) -> None:
        self.assertEqual(roi_score(), 0.5)


if __name__ == "__main__":
    unittest.main()
