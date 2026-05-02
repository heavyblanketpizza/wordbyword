from __future__ import annotations

import json
import unittest

from wordbyword.dictionary_build.example_generation import (
    BATCH_REPAIR_GENERATED_EXAMPLE_SYSTEM_PROMPT,
    build_generated_dictionary_candidate_outputs,
    retry_generated_dictionary_candidate_outputs,
)
from wordbyword.dictionary_build.llm import CompletionResult


class ExampleGenerationTest(unittest.TestCase):
    def test_generated_examples_only_replace_unlinked_rows(self) -> None:
        client = GeneratedExampleClient()

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "학교",
                    example="원래 학교 예문이다.",
                    example_replaced_with_cleaned_sentence=False,
                ),
                dictionary_candidate(
                    "버스",
                    example="아침마다 버스를 타고 회사에 간다.",
                    example_replaced_with_cleaned_sentence=True,
                ),
            ],
            [
                candidate_term("학교", pos_group="noun", quality_score=0.9),
                candidate_term("버스", pos_group="noun", quality_score=0.8),
            ],
            client=client,
        )

        self.assertEqual(outputs.reviewed_count, 1)
        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.preserved_count, 1)
        self.assertEqual(client.batch_sizes, [1])
        self.assertEqual(outputs.rows[0]["example"], "학생들이 학교 운동장에서 축구를 한다.")
        self.assertTrue(outputs.rows[0]["example_generated_with_ollama"])
        self.assertFalse(outputs.rows[0]["example_replaced_with_cleaned_sentence"])
        self.assertEqual(outputs.rows[1]["example"], "아침마다 버스를 타고 회사에 간다.")
        self.assertFalse(outputs.rows[1]["example_generated_with_ollama"])
        self.assertTrue(outputs.rows[1]["example_replaced_with_cleaned_sentence"])
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.attempt_round_count, 1)

    def test_generated_examples_batch_in_groups_of_forty(self) -> None:
        client = GeneratedExampleClient()
        snapshots: list[tuple[int, int, int]] = []

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "사과",
                    example=f"원래 사과 예문 {index}.",
                    example_replaced_with_cleaned_sentence=False,
                )
                for index in range(41)
            ],
            [candidate_term("사과", pos_group="noun", quality_score=0.9)],
            client=client,
            on_batch_complete=lambda snapshot: snapshots.append(
                (
                    snapshot.summary.batch_number,
                    sum(1 for row in snapshot.rows if bool(row["example_generated_with_ollama"])),
                    len(snapshot.unresolved_failure_rows),
                )
            ),
        )

        self.assertEqual(client.batch_sizes, [40, 1])
        self.assertEqual(snapshots, [(1, 40, 0), (2, 41, 0)])
        self.assertEqual(outputs.reviewed_count, 41)
        self.assertEqual(outputs.generated_count, 41)
        self.assertEqual(outputs.preserved_count, 0)
        self.assertTrue(all(row["example_generated_with_ollama"] for row in outputs.rows))
        self.assertEqual(outputs.failed_count, 0)

    def test_generated_examples_log_failures_without_aborting_output(self) -> None:
        client = FailingGeneratedExampleClient()

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "비슷하다",
                    example="비슷하다",
                    example_replaced_with_cleaned_sentence=False,
                )
            ],
            [candidate_term("비슷하다", pos_group="adjective", quality_score=0.9)],
            client=client,
        )

        self.assertEqual(outputs.reviewed_count, 1)
        self.assertEqual(outputs.generated_count, 0)
        self.assertEqual(outputs.failed_count, 1)
        self.assertEqual(outputs.attempt_round_count, 1)
        self.assertEqual(outputs.rows[0]["example"], "비슷하다")
        self.assertFalse(outputs.rows[0]["example_generated_with_ollama"])
        self.assertEqual(len(outputs.attempt_rows), 1)
        self.assertEqual(outputs.attempt_rows[0]["generated_sentence"], "두 그림은 서로 닮은 점이 많다.")
        self.assertIn("did not cover canonical key '비슷하다'", outputs.attempt_rows[0]["failure_reason"])
        self.assertEqual(outputs.failure_rows, outputs.attempt_rows)

    def test_generated_examples_accept_safe_surface_variant_coverage(self) -> None:
        client = SurfaceCoverageGeneratedExampleClient()

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "찾아보다",
                    example="찾아보자.",
                    example_replaced_with_cleaned_sentence=False,
                )
            ],
            [
                candidate_term(
                    "찾아보다",
                    pos_group="verb",
                    quality_score=0.9,
                    variants="찾아보 | 찾아보자 | 찾아봐요",
                )
            ],
            client=client,
        )

        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.rows[0]["example"], "모르는 단어는 국어사전에서 찾아보세요.")
        self.assertTrue(outputs.rows[0]["example_generated_with_ollama"])

    def test_generated_examples_retry_can_keep_first_pass_sentence_without_coverage_check(self) -> None:
        client = KeepGeneratedRetryClient()
        seen_attempts: list[tuple[int, int]] = []
        snapshots: list[tuple[int, int, int, int]] = []

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "비슷하다",
                    example="비슷하다",
                    example_replaced_with_cleaned_sentence=False,
                )
            ],
            [candidate_term("비슷하다", pos_group="adjective", quality_score=0.9)],
            client=client,
            on_batch_complete=lambda snapshot: snapshots.append(
                (
                    snapshot.summary.attempt_number,
                    snapshot.summary.failed_count,
                    sum(1 for row in snapshot.rows if bool(row["example_generated_with_ollama"])),
                    len(snapshot.unresolved_failure_rows),
                )
            ),
            on_attempt_complete=lambda summary: seen_attempts.append(
                (summary.attempt_number, summary.failed_count)
            ) or summary.failed_count > 0,
        )

        self.assertEqual(seen_attempts, [(1, 1), (2, 0)])
        self.assertEqual(snapshots, [(1, 1, 0, 1), (2, 0, 1, 0)])
        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.attempt_round_count, 2)
        self.assertEqual(outputs.rows[0]["example"], "두 그림은 서로 닮은 점이 많다.")
        self.assertTrue(outputs.rows[0]["example_generated_with_ollama"])
        self.assertEqual(len(outputs.attempt_rows), 2)
        self.assertFalse(outputs.attempt_rows[0]["accepted"])
        self.assertTrue(outputs.attempt_rows[1]["accepted"])
        self.assertEqual(outputs.attempt_rows[1]["attempt_kind"], "repair")
        self.assertEqual(outputs.attempt_rows[1]["review_action"], "keep_generated")
        self.assertEqual(outputs.attempt_rows[1]["input_generated_sentence"], "두 그림은 서로 닮은 점이 많다.")
        self.assertEqual(outputs.failure_rows, [])
        self.assertEqual(client.repair_prompt_row["generated_example"], "두 그림은 서로 닮은 점이 많다.")
        self.assertEqual(client.repair_prompt_row["source_example"], "비슷하다")
        self.assertIn("did not cover canonical key '비슷하다'", client.repair_prompt_row["failure_reason"])
        self.assertEqual(client.repair_system_prompt, BATCH_REPAIR_GENERATED_EXAMPLE_SYSTEM_PROMPT)

    def test_generated_examples_retry_prompt_includes_duplicate_failure_reason(self) -> None:
        client = DuplicateThenRewriteRetryClient()

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "학교",
                    example="원래 학교 예문이다.",
                    example_replaced_with_cleaned_sentence=False,
                ),
                dictionary_candidate(
                    "책",
                    example="도서관에서 책을 읽는다.",
                    example_replaced_with_cleaned_sentence=True,
                ),
            ],
            [
                candidate_term("학교", pos_group="noun", quality_score=0.9),
                candidate_term("책", pos_group="noun", quality_score=0.8),
            ],
            client=client,
            on_attempt_complete=lambda summary: summary.failed_count > 0,
        )

        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.rows[0]["example"], "학생들이 학교 운동장에서 축구를 한다.")
        self.assertEqual(outputs.attempt_rows[1]["review_action"], "rewrite_generated")
        self.assertEqual(client.repair_prompt_row["generated_example"], "도서관에서 책을 읽는다.")
        self.assertEqual(
            client.repair_prompt_row["failure_reason"],
            "Generated example for row 'r0001' duplicated an existing example sentence.",
        )

    def test_generated_example_repair_failure_reason_names_bopsida_instruction(self) -> None:
        client = InstructionThenKeepRetryClient()

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "정리",
                    example="보기 발명하고 싶은 물건의 특징이 잘 드러나도록 와 같이 2 설명할 내용을 정리해 봅시다.",
                    example_replaced_with_cleaned_sentence=False,
                )
            ],
            [candidate_term("정리", pos_group="noun", quality_score=0.9)],
            client=client,
            on_attempt_complete=lambda summary: summary.attempt_number == 1 and summary.failed_count > 0,
        )

        self.assertEqual(outputs.generated_count, 0)
        self.assertEqual(outputs.failed_count, 1)
        self.assertEqual(outputs.attempt_round_count, 2)
        self.assertEqual(len(outputs.attempt_rows), 2)
        self.assertEqual(outputs.attempt_rows[1]["attempt_kind"], "repair")
        self.assertEqual(outputs.attempt_rows[1]["review_action"], "keep_generated")
        self.assertEqual(
            outputs.attempt_rows[1]["failure_reason"],
            "Generated example repair for row 'r0001' was not a usable sentence: "
            "it is an instruction ending with '-봅시다'.",
        )

    def test_generated_examples_retry_can_rewrite_source_for_quality(self) -> None:
        client = RewriteSourceRetryClient()

        outputs = build_generated_dictionary_candidate_outputs(
            [
                dictionary_candidate(
                    "비슷하다",
                    example="두 그림은 비슷한 분위기예요.",
                    example_replaced_with_cleaned_sentence=False,
                )
            ],
            [candidate_term("비슷하다", pos_group="adjective", quality_score=0.9)],
            client=client,
            on_attempt_complete=lambda summary: summary.failed_count > 0,
        )

        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.attempt_round_count, 2)
        self.assertEqual(outputs.rows[0]["example"], "두 그림의 분위기가 아주 비슷하다.")
        self.assertEqual(outputs.attempt_rows[1]["review_action"], "rewrite_source")
        self.assertTrue(outputs.attempt_rows[1]["accepted"])

    def test_retry_generated_examples_auto_accepts_saved_failure_before_repair(self) -> None:
        outputs = retry_generated_dictionary_candidate_outputs(
            [
                {
                    **dictionary_candidate(
                        "찾아보다",
                        example="찾아보자.",
                        example_replaced_with_cleaned_sentence=False,
                    ),
                    "example_generated_with_ollama": False,
                }
            ],
            [
                {
                    "attempt_number": 1,
                    "attempt_kind": "generate",
                    "row_id": "r0001",
                    "canonical_key": "찾아보다",
                    "display": "찾아보다",
                    "source_example": "찾아보자.",
                    "input_generated_sentence": "",
                    "review_action": "",
                    "generated_sentence": "모르는 단어는 국어사전에서 찾아보세요.",
                    "accepted": False,
                    "failure_reason": "Generated example for row 'r0001' did not cover canonical key '찾아보다'.",
                }
            ],
            [
                candidate_term(
                    "찾아보다",
                    pos_group="verb",
                    quality_score=0.9,
                    variants="찾아보 | 찾아보자 | 찾아봐요",
                )
            ],
            client=UnexpectedRetryClient(),
            starting_attempt_number=2,
        )

        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.attempt_round_count, 1)
        self.assertEqual(outputs.attempt_rows, [])
        self.assertEqual(outputs.failure_rows, [])
        self.assertEqual(outputs.rows[0]["example"], "모르는 단어는 국어사전에서 찾아보세요.")
        self.assertTrue(outputs.rows[0]["example_generated_with_ollama"])


def candidate_term(
    canonical_key: str,
    *,
    pos_group: str,
    quality_score: float,
    variants: object | None = None,
) -> dict[str, object]:
    return {
        "canonical_key": canonical_key,
        "display": canonical_key,
        "kind": "term",
        "pos_group": pos_group,
        "count": 10,
        "surface_count": 10,
        "document_count": 1,
        "variant_count": 1,
        "variants": canonical_key if variants is None else variants,
        "quality_score": quality_score,
        "roi_score": 0.5,
        "example": f"{canonical_key} 예문",
    }


def dictionary_candidate(
    canonical_key: str,
    *,
    example: str,
    example_replaced_with_cleaned_sentence: bool,
) -> dict[str, object]:
    return {
        "canonical_key": canonical_key,
        "display": canonical_key,
        "kind": "term",
        "variants": [canonical_key],
        "count": 10,
        "document_count": 1,
        "quality_score": 0.8,
        "roi_score": 0.5,
        "example": example,
        "curation_status": "rewrite_ready",
        "example_replaced_with_cleaned_sentence": example_replaced_with_cleaned_sentence,
    }


class GeneratedExampleClient:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        self.batch_sizes.append(len(rows))
        lines = []
        for row in rows:
            assert_prompt_packet_shape(row)
            headword = str(row["headword"])
            if headword == "학교":
                sentence = "학생들이 학교 운동장에서 축구를 한다."
            elif headword == "버스":
                sentence = "아침마다 버스를 타고 회사에 간다."
            else:
                sentence = f"학생 {row['id'][1:]}이 사과를 먹는다."
            lines.append(f"{row['id']}\t{sentence}")
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines),
        )


class FailingGeneratedExampleClient:
    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        row = json.loads(messages[-1].content.splitlines()[1])
        assert_prompt_packet_shape(row)
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=f"{row['id']}\t두 그림은 서로 닮은 점이 많다.",
        )


class SurfaceCoverageGeneratedExampleClient:
    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        row = json.loads(messages[-1].content.splitlines()[1])
        assert_prompt_packet_shape(row)
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=f"{row['id']}\t모르는 단어는 국어사전에서 찾아보세요.",
        )


class DuplicateThenRewriteRetryClient:
    def __init__(self) -> None:
        self.calls = 0
        self.repair_prompt_row: dict[str, object] = {}

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        self.calls += 1
        row = json.loads(messages[-1].content.splitlines()[1])
        if self.calls == 1:
            assert_prompt_packet_shape(row)
            content = f"{row['id']}\t도서관에서 책을 읽는다."
        else:
            assert_repair_prompt_packet_shape(row)
            self.repair_prompt_row = row
            content = f"{row['id']}\trewrite_generated\t학생들이 학교 운동장에서 축구를 한다."
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=content,
        )


class InstructionThenKeepRetryClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        self.calls += 1
        row = json.loads(messages[-1].content.splitlines()[1])
        sentence = "발표할 내용을 간단하게 정리해 봅시다."
        if self.calls == 1:
            assert_prompt_packet_shape(row)
            content = f"{row['id']}\t{sentence}"
        else:
            assert_repair_prompt_packet_shape(row)
            content = f"{row['id']}\tkeep_generated\t"
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=content,
        )


class KeepGeneratedRetryClient:
    def __init__(self) -> None:
        self.calls = 0
        self.repair_prompt_row: dict[str, object] = {}
        self.repair_system_prompt = ""

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        self.calls += 1
        row = json.loads(messages[-1].content.splitlines()[1])
        if self.calls == 1:
            assert_prompt_packet_shape(row)
            sentence = "두 그림은 서로 닮은 점이 많다."
            content = f"{row['id']}\t{sentence}"
        else:
            assert_repair_prompt_packet_shape(row)
            self.repair_prompt_row = row
            self.repair_system_prompt = messages[0].content
            content = f"{row['id']}\tkeep_generated\t"
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=content,
        )


class RewriteSourceRetryClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        self.calls += 1
        row = json.loads(messages[-1].content.splitlines()[1])
        if self.calls == 1:
            assert_prompt_packet_shape(row)
            content = f"{row['id']}\t두 그림은 서로 닮은 점이 많다."
        else:
            assert_repair_prompt_packet_shape(row)
            content = f"{row['id']}\trewrite_source\t두 그림의 분위기가 아주 비슷하다."
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=content,
        )


class UnexpectedRetryClient:
    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        raise AssertionError("retry client should not be called for auto-accepted failures")


def assert_prompt_packet_shape(row: dict[str, object]) -> None:
    assert set(row) == {"id", "headword", "variants"}


def assert_repair_prompt_packet_shape(row: dict[str, object]) -> None:
    assert set(row) == {
        "id",
        "headword",
        "variants",
        "source_example",
        "generated_example",
        "failure_reason",
    }


if __name__ == "__main__":
    unittest.main()
