from __future__ import annotations

import unittest

from wordbyword.dictionary_build.meaning_assignment import build_dictionary_candidates_with_meanings


class MeaningAssignmentTest(unittest.TestCase):
    def test_adds_reference_meanings_by_canonical_key(self) -> None:
        outputs = build_dictionary_candidates_with_meanings(
            [
                dictionary_candidate("학교"),
                dictionary_candidate("없는말"),
            ],
            [
                {"canonical_key": "학교", "meaning": "학생들이 배우는 곳."},
                {"canonical_key": "버스", "meaning": "여러 사람이 함께 타는 차."},
            ],
        )

        self.assertEqual(outputs.matched_count, 1)
        self.assertEqual(outputs.unmatched_count, 1)
        self.assertEqual(outputs.rows[0]["meaning"], "학생들이 배우는 곳.")
        self.assertEqual(outputs.rows[1]["meaning"], "")
        self.assertTrue(all(row["curation_status"] == "confirmed" for row in outputs.rows))
        self.assertEqual([row["canonical_key"] for row in outputs.rows], ["학교", "없는말"])

    def test_first_reference_record_wins_for_duplicate_keys(self) -> None:
        outputs = build_dictionary_candidates_with_meanings(
            [dictionary_candidate("물")],
            [
                {"canonical_key": "물", "meaning": "마시는 액체."},
                {"canonical_key": "물", "meaning": "강이나 바다."},
            ],
        )

        self.assertEqual(outputs.rows[0]["meaning"], "마시는 액체.")

    def test_calls_incremental_callback_for_each_output_row(self) -> None:
        checkpointed_rows: list[dict[str, object]] = []

        outputs = build_dictionary_candidates_with_meanings(
            [
                dictionary_candidate("물"),
                dictionary_candidate("책"),
            ],
            [{"canonical_key": "물", "meaning": "마시는 액체."}],
            on_row=checkpointed_rows.append,
        )

        self.assertEqual(checkpointed_rows, outputs.rows)


def dictionary_candidate(canonical_key: str) -> dict[str, object]:
    return {
        "canonical_key": canonical_key,
        "display": canonical_key,
        "kind": "term",
        "example": f"{canonical_key} 예문",
    }


if __name__ == "__main__":
    unittest.main()
