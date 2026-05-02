from __future__ import annotations

import unittest

from wordbyword.dictionary_build.example_assignment import build_linked_dictionary_candidate_outputs


class ExampleAssignmentTest(unittest.TestCase):
    def test_linked_dictionary_candidates_greedily_assign_unique_cleaned_sentences(self) -> None:
        outputs = build_linked_dictionary_candidate_outputs(
            [
                dictionary_candidate("먹다", quality_score=0.8, example="원래 예문 2"),
                dictionary_candidate("가다", quality_score=0.9, example="원래 예문 1"),
                dictionary_candidate("보다", quality_score=0.7, example="원래 예문 3"),
            ],
            [
                cleaned_sentence(
                    "학생이 학교에 간다.",
                    quality_score=0.95,
                    covered_keys="학생 | 학교 | 가다",
                ),
                cleaned_sentence(
                    "점심을 맛있게 먹었다.",
                    quality_score=0.92,
                    covered_keys="점심 | 맛있다 | 먹다",
                ),
            ],
        )

        self.assertEqual(outputs.linked_count, 2)
        self.assertEqual(outputs.unlinked_count, 1)
        self.assertEqual([row["canonical_key"] for row in outputs.rows], ["가다", "먹다", "보다"])
        self.assertEqual(outputs.rows[0]["example"], "학생이 학교에 간다.")
        self.assertTrue(outputs.rows[0]["example_replaced_with_cleaned_sentence"])
        self.assertEqual(outputs.rows[0]["curation_status"], "rewrite_ready")
        self.assertEqual(outputs.rows[1]["example"], "점심을 맛있게 먹었다.")
        self.assertTrue(outputs.rows[1]["example_replaced_with_cleaned_sentence"])
        self.assertEqual(outputs.rows[2]["example"], "원래 예문 3")
        self.assertFalse(outputs.rows[2]["example_replaced_with_cleaned_sentence"])
        self.assertEqual(outputs.rows[2]["curation_status"], "rewrite_ready")
        self.assertEqual(
            list(outputs.rows[0].keys())[:6],
            [
                "canonical_key",
                "display",
                "kind",
                "variants",
                "count",
                "document_count",
            ],
        )
        self.assertEqual(list(outputs.rows[0].keys())[-1], "example_replaced_with_cleaned_sentence")
        self.assertNotIn("original_example", outputs.rows[0])
        self.assertNotIn("cleaned_example_document", outputs.rows[0])
        self.assertNotIn("cleaned_example_sentence_index", outputs.rows[0])
        self.assertNotIn("cleaned_example_quality_score", outputs.rows[0])
        self.assertNotIn("cleaned_example_covered_canonical_keys", outputs.rows[0])

    def test_linked_dictionary_candidates_strip_old_stage_twelve_fields(self) -> None:
        outputs = build_linked_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "물",
                    quality_score=0.9,
                    example="원래 예문",
                    extra_fields={
                        "original_example": "이전 예문",
                        "cleaned_example_sentence_index": "3",
                    },
                )
            ],
            [
                cleaned_sentence(
                    "지구에는 물이 많지만 사람이 쓸 수 있는 물은 적어요.",
                    quality_score=0.95,
                    covered_keys="지구 | 물 | 많다 | 사람 | 쓰다 | 수 | 적다",
                )
            ],
        )

        row = outputs.rows[0]
        self.assertEqual(row["example"], "지구에는 물이 많지만 사람이 쓸 수 있는 물은 적어요.")
        self.assertEqual(row["curation_status"], "rewrite_ready")
        self.assertTrue(row["example_replaced_with_cleaned_sentence"])
        self.assertNotIn("original_example", row)
        self.assertNotIn("cleaned_example_sentence_index", row)


def dictionary_candidate(
    canonical_key: str,
    *,
    quality_score: float,
    example: str,
    extra_fields: dict[str, object] | None = None,
) -> dict[str, object]:
    row = {
        "canonical_key": canonical_key,
        "display": canonical_key,
        "kind": "term",
        "variants": [canonical_key],
        "count": 10,
        "document_count": 1,
        "quality_score": quality_score,
        "roi_score": None,
        "example": example,
        "curation_status": "candidate",
    }
    if extra_fields:
        row.update(extra_fields)
    return row


def cleaned_sentence(sentence: str, *, quality_score: float, covered_keys: str) -> dict[str, object]:
    return {
        "document": "sample.txt",
        "page": "1",
        "sentence_index": "1",
        "cleaned_sentence": sentence,
        "cleaned_quality_score": quality_score,
        "cleanup_action": "keep",
        "cleaned_covered_canonical_keys": covered_keys,
    }


if __name__ == "__main__":
    unittest.main()
