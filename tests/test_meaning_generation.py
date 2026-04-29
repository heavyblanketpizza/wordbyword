from __future__ import annotations

import json
import unittest

from wordbyword.llm import CompletionResult
from wordbyword.meaning_generation import build_generated_meaning_dictionary_outputs


class MeaningGenerationTest(unittest.TestCase):
    def test_generates_missing_meanings_in_batches_of_forty(self) -> None:
        client = GeneratedMeaningClient()
        snapshots: list[tuple[int, int, int]] = []

        outputs = build_generated_meaning_dictionary_outputs(
            [
                dictionary_candidate(
                    "없는말",
                    example=f"없는말 예문 {index}.",
                    meaning="",
                )
                for index in range(41)
            ],
            client=client,
            on_batch_complete=lambda snapshot: snapshots.append(
                (
                    snapshot.batch_number,
                    snapshot.generated_count,
                    sum(1 for row in snapshot.rows if bool(row["meaning_generated_with_ollama"])),
                )
            ),
        )

        self.assertEqual(client.batch_sizes, [40, 1])
        self.assertEqual(snapshots, [(1, 40, 40), (2, 41, 41)])
        self.assertEqual(outputs.reviewed_count, 41)
        self.assertEqual(outputs.generated_count, 41)
        self.assertEqual(outputs.preserved_count, 0)
        self.assertEqual(outputs.missing_count, 0)
        self.assertTrue(all(row["meaning"] == "예문에서 쓰인 뜻." for row in outputs.rows))
        self.assertTrue(all(row["meaning_generated_with_ollama"] for row in outputs.rows))
        self.assertTrue(all(row["curation_status"] == "confirmed" for row in outputs.rows))

    def test_preserves_existing_meanings(self) -> None:
        outputs = build_generated_meaning_dictionary_outputs(
            [
                dictionary_candidate("학교", meaning="학생들이 배우는 곳."),
                dictionary_candidate("없는말", meaning=""),
            ],
            client=GeneratedMeaningClient(),
        )

        self.assertEqual(outputs.reviewed_count, 1)
        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.preserved_count, 1)
        self.assertEqual(outputs.rows[0]["meaning"], "학생들이 배우는 곳.")
        self.assertFalse(outputs.rows[0]["meaning_generated_with_ollama"])
        self.assertEqual(outputs.rows[1]["meaning"], "예문에서 쓰인 뜻.")
        self.assertTrue(outputs.rows[1]["meaning_generated_with_ollama"])


def dictionary_candidate(
    canonical_key: str,
    *,
    example: str = "예문입니다.",
    meaning: str,
) -> dict[str, object]:
    return {
        "canonical_key": canonical_key,
        "display": canonical_key,
        "kind": "term",
        "example": example,
        "curation_status": "confirmed",
        "meaning": meaning,
    }


class GeneratedMeaningClient:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        self.batch_sizes.append(len(rows))
        self.last_prompt_row = rows[-1]
        lines = [f"{row['id']}\t예문에서 쓰인 뜻." for row in rows]
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines),
        )


if __name__ == "__main__":
    unittest.main()
