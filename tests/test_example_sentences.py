from __future__ import annotations

import unittest

from wordbyword.writers import (
    EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM,
    EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM,
    FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM,
    example_sentence_sort_score,
    example_sentence_rows,
    filtered_example_sentence_rows,
    filtered_high_signal_example_sentence_rows,
)


class ExampleSentenceTest(unittest.TestCase):
    def test_sentence_quality_uses_covered_keys_not_repeated_matches(self) -> None:
        rows = example_sentence_rows(
            sentences=[
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "sentence": "물을 보고 물을 먹고 친구 책 학교를 보았다.",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "sentence": "물을 보고 먹고 친구 책을 보았다.",
                },
            ],
            tokens=[
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 1,
                    "form": "물",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 2,
                    "form": "보",
                    "pos": "VV",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 3,
                    "form": "물",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 4,
                    "form": "먹",
                    "pos": "VV",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 5,
                    "form": "친구",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 6,
                    "form": "책",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 7,
                    "form": "학교",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "token_index": 1,
                    "form": "물",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "token_index": 2,
                    "form": "보",
                    "pos": "VV",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "token_index": 3,
                    "form": "먹",
                    "pos": "VV",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "token_index": 4,
                    "form": "친구",
                    "pos": "NNG",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "token_index": 5,
                    "form": "책",
                    "pos": "NNG",
                },
            ],
            candidate_terms=[
                {"canonical_key": "물", "quality_score": 0.7},
                {"canonical_key": "보다", "quality_score": 0.3},
                {"canonical_key": "먹다", "quality_score": 0.4},
                {"canonical_key": "친구", "quality_score": 0.2},
                {"canonical_key": "책", "quality_score": 0.5},
                {"canonical_key": "학교", "quality_score": 0.6},
            ],
        )

        self.assertEqual(len(rows), 2)
        row_by_sentence = {row["sentence"]: row for row in rows}
        repeated = row_by_sentence["물을 보고 물을 먹고 친구 책 학교를 보았다."]
        shorter = row_by_sentence["물을 보고 먹고 친구 책을 보았다."]
        self.assertEqual(repeated["matched_canonical_keys"], "물 | 보다 | 물 | 먹다 | 친구 | 책 | 학교")
        self.assertEqual(repeated["covered_canonical_keys"], "물 | 보다 | 먹다 | 친구 | 책 | 학교")
        self.assertEqual(repeated["quality_score"], 1.0)
        self.assertEqual(shorter["matched_canonical_keys"], "물 | 보다 | 먹다 | 친구 | 책")
        self.assertEqual(shorter["covered_canonical_keys"], "물 | 보다 | 먹다 | 친구 | 책")
        self.assertEqual(shorter["quality_score"], 0.778)
        self.assertEqual(rows[0]["sentence"], "물을 보고 먹고 친구 책을 보았다.")

    def test_example_sentence_prefilter_keeps_only_mid_coverage_rows(self) -> None:
        rows = [
            example_row("too small.", quality_score=0.8, matched_count=4, covered_count=4),
            example_row("keep min.", quality_score=0.8, matched_count=5, covered_count=5),
            example_row("keep max.", quality_score=0.8, matched_count=15, covered_count=15),
            example_row("too large.", quality_score=0.8, matched_count=16, covered_count=16),
        ]

        filtered = filtered_example_sentence_rows(rows)

        self.assertEqual([row["sentence"] for row in filtered], ["keep min.", "keep max."])
        self.assertTrue(
            all(
                EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM
                <= int(row["covered_canonical_key_count"])
                <= EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM
                for row in filtered
            )
        )

    def test_example_sentence_prefilter_applies_density_and_sentence_shape_rules(self) -> None:
        rows = [
            example_row("keep.", quality_score=0.125, matched_count=5),
            example_row("too sparse.", quality_score=0.065, matched_count=5),
            example_row("no terminal punctuation", quality_score=0.8, matched_count=5),
            example_row("같이 읽어 봅시다.", quality_score=0.8, matched_count=5),
            example_row('quoted sentence"', quality_score=0.8, matched_count=5),
        ]

        filtered = filtered_high_signal_example_sentence_rows(rows)

        self.assertEqual([row["sentence"] for row in filtered], ["keep.", 'quoted sentence"'])
        self.assertAlmostEqual(
            example_sentence_sort_score(rows[1]),
            FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM - 0.0005,
        )


def example_row(
    sentence: str,
    *,
    quality_score: float,
    matched_count: int,
    covered_count: int = 5,
) -> dict[str, object]:
    covered_keys = [f"{sentence}-covered-{index}" for index in range(covered_count)]
    matched_keys = covered_keys + [covered_keys[-1]] * max(0, matched_count - covered_count)
    return {
        "document": "sample.txt",
        "page": "",
        "sentence_index": 1,
        "sentence": sentence,
        "quality_score": quality_score,
        "matched_canonical_key_count": matched_count,
        "covered_canonical_key_count": covered_count,
        "covered_canonical_keys": " | ".join(covered_keys),
        "matched_canonical_keys": " | ".join(matched_keys),
    }


if __name__ == "__main__":
    unittest.main()
