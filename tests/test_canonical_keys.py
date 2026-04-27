from __future__ import annotations

import unittest

from wordbyword.canonical_keys import (
    join_keys,
    ordered_unique,
    row_covered_canonical_keys,
    sentence_candidate_keys,
    split_joined_keys,
    token_canonical_key,
)


class CanonicalKeysTest(unittest.TestCase):
    def test_sentence_candidate_keys_merges_noun_plus_xsv_into_hada(self) -> None:
        matched_keys = sentence_candidate_keys(
            [
                {"form": "공부", "pos": "NNG"},
                {"form": "하", "pos": "XSV"},
                {"form": "학생", "pos": "NNG"},
            ],
            {"공부하다", "학생"},
        )

        self.assertEqual(matched_keys, ["공부하다", "학생"])

    def test_sentence_candidate_keys_only_merges_known_hada_forms(self) -> None:
        matched_keys = sentence_candidate_keys(
            [
                {"form": "공부", "pos": "NNG"},
                {"form": "하", "pos": "XSV"},
                {"form": "학생", "pos": "NNG"},
            ],
            {"공부", "학생"},
        )

        self.assertEqual(matched_keys, ["공부", "학생"])

    def test_token_canonical_key_normalizes_verbs_and_ignores_particles(self) -> None:
        self.assertEqual(token_canonical_key("읽", "VV"), "읽다")
        self.assertEqual(token_canonical_key("학생", "NNG"), "학생")
        self.assertIsNone(token_canonical_key("은", "JX"))

    def test_key_list_helpers_round_trip_and_preserve_order(self) -> None:
        values = ["읽다", "학생", "읽다", "학교"]

        self.assertEqual(ordered_unique(values), ["읽다", "학생", "학교"])
        self.assertEqual(join_keys(["읽다", "학생"]), "읽다 | 학생")
        self.assertEqual(split_joined_keys(" 읽다 | 학생 | 학교 "), ["읽다", "학생", "학교"])

    def test_row_covered_canonical_keys_uses_first_populated_supported_field(self) -> None:
        self.assertEqual(
            row_covered_canonical_keys(
                {
                    "cleaned_covered_canonical_keys": "",
                    "covered_canonical_keys": "읽다 | 학생",
                    "source_covered_canonical_keys": "가다",
                }
            ),
            ["읽다", "학생"],
        )
        self.assertEqual(
            row_covered_canonical_keys(
                {
                    "cleaned_covered_canonical_keys": "",
                    "covered_canonical_keys": "",
                    "source_covered_canonical_keys": "가다 | 학교",
                }
            ),
            ["가다", "학교"],
        )
        self.assertEqual(
            row_covered_canonical_keys(
                {
                    "cleaned_covered_canonical_keys": "읽다 | 학생",
                    "covered_canonical_keys": "가다 | 학교",
                },
                field_names=("cleaned_covered_canonical_keys",),
            ),
            ["읽다", "학생"],
        )


if __name__ == "__main__":
    unittest.main()
