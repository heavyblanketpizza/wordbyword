from __future__ import annotations

import unittest

from wordbyword.example_cleanup import (
    CleanupBatchProgress,
    build_cleaned_example_sentence_outputs,
    clean_example_sentence,
    cleanup_batch_sizes,
    review_cleanup_batches,
)
from wordbyword.llm import CompletionResult


class ExampleCleanupTest(unittest.TestCase):
    def test_cleanup_batch_sizes_defaults_to_batches_of_fifty(self) -> None:
        sizes = cleanup_batch_sizes(1042)

        self.assertEqual(sum(sizes), 1042)
        self.assertEqual(sizes, ([50] * 20) + [42])
        self.assertTrue(all(size == 50 for size in sizes[:-1]))
        self.assertEqual(sizes[-1], 42)

    def test_cleanup_batch_sizes_accepts_small_single_batch(self) -> None:
        self.assertEqual(cleanup_batch_sizes(42), [42])

    def test_clean_example_sentence_keep_preserves_source_sentence(self) -> None:
        client = BatchCompletionClient(
            [
                "EMPTY",
            ]
        )

        result = clean_example_sentence(
            client,
            sentence="점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다.",
        )

        self.assertEqual(result["action"], "keep")
        self.assertEqual(result["cleaned_sentence"], "점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다.")
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["confidence"], 0.0)

    def test_build_cleaned_example_sentence_outputs_keeps_fixes_and_drops_rejects(self) -> None:
        outputs = build_cleaned_example_sentence_outputs(
            [
                example_row(
                    "sample.txt",
                    1,
                    "점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다.",
                    0.8,
                    "점심 | 먹다 | 뒤 | 어머니 | 함께 | 놀이터 | 나가다",
                ),
                example_row(
                    "sample.txt",
                    2,
                    "이가 아픈 여우가 드소토 선생님을 찾아옴.",
                    0.7,
                    "이 | 아프다 | 여우 | 드소토 | 선생 | 찾다 | 오다",
                ),
                example_row(
                    "sample.txt",
                    3,
                    "점선을 따라 뜯어 사용하세요.",
                    0.5,
                    "점선 | 따르다 | 뜯다 | 사용하다 | 점",
                ),
                example_row(
                    "sample.txt",
                    4,
                    "제가 좋아하는 이야기는 「토끼와 호랑이」입니다.",
                    0.6,
                    "저 | 좋아하다 | 이야기 | 토끼 | 호랑이",
                ),
            ],
            [
                candidate("점심", 0.6),
                candidate("먹다", 0.7),
                candidate("뒤", 0.4),
                candidate("어머니", 0.8),
                candidate("함께", 0.5),
                candidate("놀이터", 0.7),
                candidate("나가다", 0.8),
                candidate("이", 0.3),
                candidate("아프다", 0.8),
                candidate("여우", 0.7),
                candidate("드소토", 0.4),
                candidate("선생", 0.5),
                candidate("찾다", 0.6),
                candidate("오다", 0.6),
                candidate("점선", 0.3),
                candidate("따르다", 0.4),
                candidate("뜯다", 0.5),
                candidate("사용하다", 0.6),
                candidate("점", 0.2),
                candidate("저", 0.4),
                candidate("좋아하다", 0.8),
                candidate("이야기", 0.7),
                candidate("토끼", 0.5),
                candidate("호랑이", 0.5),
            ],
            client=BatchCompletionClient(
                [
                    "\n".join(
                        [
                            "r0002\tlight_fix\t이가 아픈 여우가 드소토 선생님을 찾아왔다.",
                            "r0003\treject\t",
                            "r0004\tlight_fix\t나는 동화를 좋아합니다.",
                        ]
                    )
                ]
            ),
        )

        self.assertEqual(outputs.reviewed_count, 4)
        self.assertEqual(outputs.rejected_count, 2)
        self.assertEqual(outputs.action_counts["keep"], 1)
        self.assertEqual(outputs.action_counts["light_fix"], 1)
        self.assertEqual(outputs.action_counts["rebuild"], 0)
        self.assertEqual(outputs.action_counts["reject"], 2)
        self.assertEqual(len(outputs.rows), 2)
        self.assertEqual([row["cleanup_action"] for row in outputs.rows], ["keep", "light_fix"])
        self.assertEqual(outputs.rows[0]["cleaned_sentence"], outputs.rows[0]["source_sentence"])
        self.assertEqual(outputs.rows[0]["cleanup_reason"], "")
        self.assertEqual(float(outputs.rows[0]["model_confidence"]), 0.0)
        self.assertEqual(outputs.rows[1]["cleaned_sentence"], "이가 아픈 여우가 드소토 선생님을 찾아왔다.")
        self.assertGreaterEqual(int(outputs.rows[1]["cleaned_covered_canonical_key_count"]), 5)
        self.assertIn("아프다", str(outputs.rows[1]["cleaned_covered_canonical_keys"]))
        self.assertEqual(max(float(row["cleaned_quality_score"]) for row in outputs.rows), 1.0)


    def test_review_cleanup_batches_emits_start_and_complete_progress_per_batch(self) -> None:
        indexed_rows = [
            (f"r{index:04d}", {"sentence": f"문장 {index}."})
            for index in range(1, 121)
        ]
        client = BatchCompletionClient(["EMPTY", "EMPTY", "EMPTY"])
        events: list[CleanupBatchProgress] = []

        review_cleanup_batches(client, indexed_rows, on_progress=events.append)

        self.assertEqual(len(events), 6)
        self.assertEqual([event.phase for event in events], [
            "start",
            "complete",
            "start",
            "complete",
            "start",
            "complete",
        ])
        self.assertEqual(events[0].batch_index, 1)
        self.assertEqual(events[0].total_batches, 3)
        self.assertEqual(events[0].completed_rows, 0)
        self.assertEqual(events[0].current_batch_size, 50)
        self.assertEqual(events[1].completed_rows, 50)
        self.assertEqual(events[3].completed_rows, 100)
        self.assertEqual(events[5].batch_index, 3)
        self.assertEqual(events[5].completed_batches, 3)
        self.assertEqual(events[5].completed_rows, 120)
        self.assertEqual(events[5].total_rows, 120)


def example_row(
    document: str,
    sentence_index: int,
    sentence: str,
    quality_score: float,
    covered_keys: str,
) -> dict[str, object]:
    covered_key_list = [part.strip() for part in covered_keys.split("|") if part.strip()]
    return {
        "document": document,
        "page": "",
        "sentence_index": sentence_index,
        "sentence": sentence,
        "quality_score": quality_score,
        "matched_canonical_key_count": len(covered_key_list),
        "covered_canonical_key_count": len(covered_key_list),
        "covered_canonical_keys": covered_keys,
        "matched_canonical_keys": covered_keys,
    }


def candidate(canonical_key: str, quality_score: float) -> dict[str, object]:
    return {
        "canonical_key": canonical_key,
        "quality_score": quality_score,
    }


class BatchCompletionClient:
    def __init__(self, payloads: list[str]) -> None:
        self.payloads = payloads
        self.index = 0

    def complete(self, *args, **kwargs) -> CompletionResult:
        payload = self.payloads[self.index]
        self.index += 1
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=payload,
        )


if __name__ == "__main__":
    unittest.main()
