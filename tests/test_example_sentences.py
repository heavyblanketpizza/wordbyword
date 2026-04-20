from __future__ import annotations

import unittest

from wordbyword.writers import (
    FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM,
    example_sentence_sort_score,
    example_sentence_rows,
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
                    "sentence": "물을 보고 물을 먹었다.",
                },
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 2,
                    "sentence": "물을 보았다.",
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
            ],
            candidate_terms=[
                {"canonical_key": "물", "quality_score": 0.7},
                {"canonical_key": "보다", "quality_score": 0.3},
                {"canonical_key": "먹다", "quality_score": 0.4},
            ],
        )

        self.assertEqual(len(rows), 2)
        row_by_sentence = {row["sentence"]: row for row in rows}
        repeated = row_by_sentence["물을 보고 물을 먹었다."]
        shorter = row_by_sentence["물을 보았다."]
        self.assertEqual(repeated["matched_canonical_keys"], "물 | 보다 | 물 | 먹다")
        self.assertEqual(repeated["covered_canonical_keys"], "물 | 보다 | 먹다")
        self.assertEqual(repeated["quality_score"], 1.0)
        self.assertEqual(shorter["matched_canonical_keys"], "물 | 보다")
        self.assertEqual(shorter["covered_canonical_keys"], "물 | 보다")
        self.assertEqual(shorter["quality_score"], 0.714)
        self.assertEqual(rows[0]["sentence"], "물을 보았다.")

    def test_filtered_example_sentences_apply_density_and_sentence_shape_rules(self) -> None:
        rows = [
            example_row("keep.", quality_score=0.05, matched_count=2),
            example_row("too sparse.", quality_score=0.026, matched_count=2),
            example_row("no terminal punctuation", quality_score=0.8, matched_count=2),
            example_row("같이 읽어 봅시다.", quality_score=0.8, matched_count=2),
            example_row('quoted sentence"', quality_score=0.8, matched_count=2),
        ]

        filtered = filtered_high_signal_example_sentence_rows(rows)

        self.assertEqual([row["sentence"] for row in filtered], ["keep.", 'quoted sentence"'])
        self.assertAlmostEqual(
            example_sentence_sort_score(rows[1]),
            FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM - 0.0005,
        )


def example_row(sentence: str, *, quality_score: float, matched_count: int) -> dict[str, object]:
    return {
        "document": "sample.txt",
        "page": "",
        "sentence_index": 1,
        "sentence": sentence,
        "quality_score": quality_score,
        "matched_canonical_key_count": matched_count,
        "covered_canonical_key_count": 2,
        "covered_canonical_keys": "가 | 나",
        "matched_canonical_keys": "가 | 나",
    }


if __name__ == "__main__":
    unittest.main()
