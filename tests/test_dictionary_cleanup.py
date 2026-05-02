from __future__ import annotations

import unittest

from wordbyword.dictionary_build.dictionary_cleanup import (
    BATCH_CLEAN_DICTIONARY_CANDIDATE_SYSTEM_PROMPT,
    DEFAULT_DICTIONARY_CLEANUP_BATCH_SIZE,
    IndexedDictionaryCandidate,
    build_cleaned_dictionary_candidate_outputs,
    dictionary_cleanup_batch_sizes,
    review_dictionary_cleanup_batch,
)
from wordbyword.dictionary_build.llm import CompletionResult


class DictionaryCleanupTest(unittest.TestCase):
    def test_dictionary_cleanup_batch_sizes_follow_the_configured_default(self) -> None:
        sizes = dictionary_cleanup_batch_sizes(104)

        self.assertEqual(sum(sizes), 104)
        self.assertEqual(
            sizes,
            [DEFAULT_DICTIONARY_CLEANUP_BATCH_SIZE, DEFAULT_DICTIONARY_CLEANUP_BATCH_SIZE, 24],
        )

    def test_review_dictionary_cleanup_prompt_rejects_names_but_keeps_common_words(self) -> None:
        client = PromptCapturingCompletionClient("EMPTY")

        review_dictionary_cleanup_batch(
            client,
            [
                indexed_candidate(
                    "r0001",
                    dictionary_candidate("드소토"),
                    candidate_term("드소토", "proper_or_specific_noun"),
                    ["드소토 선생님은 용감하게도 여우의 입안으로 들어갔어요."],
                ),
            ],
        )

        self.assertEqual(len(client.messages), 1)
        system_prompt = client.messages[0][0].content
        self.assertEqual(system_prompt, BATCH_CLEAN_DICTIONARY_CANDIDATE_SYSTEM_PROMPT)
        self.assertIn("드소토", system_prompt)
        self.assertIn("김철수", system_prompt)
        self.assertIn("Keep ordinary Korean vocabulary", system_prompt)

    def test_build_cleaned_dictionary_candidate_outputs_rejects_names_and_keeps_common_words(self) -> None:
        outputs = build_cleaned_dictionary_candidate_outputs(
            [
                dictionary_candidate("드소토", example="이가 아픈 여우가 드소토 선생님을 찾아왔다."),
                dictionary_candidate("여우", example="이가 아픈 여우가 드소토 선생님을 찾아왔다."),
                dictionary_candidate("정은희", example="글: 김소희, 그림: 정은희"),
                dictionary_candidate("문장", example="9 (1) 두 문장의 다른 점을 말해 보세요."),
            ],
            [
                candidate_term("드소토", "proper_or_specific_noun"),
                candidate_term("여우", "noun"),
                candidate_term("정은희", "proper_or_specific_noun"),
                candidate_term("문장", "noun"),
            ],
            [
                support_row(
                    "이가 아픈 여우가 드소토 선생님을 찾아왔다.",
                    "여우 | 드소토 | 선생 | 찾다 | 오다",
                ),
                support_row(
                    "두 문장의 다른 점을 말해 보세요.",
                    "두 | 문장 | 다른 | 점 | 말하다",
                ),
            ],
            client=BatchCompletionClient(
                [
                    "\n".join(
                        [
                            "r0001\treject\tproper_name",
                            "r0003\treject\tmetadata_name",
                        ]
                    )
                ]
            ),
        )

        self.assertEqual(outputs.reviewed_count, 4)
        self.assertEqual(outputs.rejected_count, 2)
        self.assertEqual(outputs.action_counts["keep"], 2)
        self.assertEqual(outputs.action_counts["reject"], 2)
        self.assertEqual(outputs.rejection_reason_counts["proper_name"], 1)
        self.assertEqual(outputs.rejection_reason_counts["metadata_name"], 1)
        self.assertEqual([row["canonical_key"] for row in outputs.rows], ["여우", "문장"])


def dictionary_candidate(canonical_key: str, *, example: str = "") -> dict[str, object]:
    return {
        "kind": "term",
        "canonical_key": canonical_key,
        "display": canonical_key,
        "variants": [canonical_key],
        "count": 3,
        "document_count": 1,
        "quality_score": 0.5,
        "roi_score": 0.5,
        "example": example or f"{canonical_key} 예문입니다.",
        "curation_status": "candidate",
    }


def candidate_term(canonical_key: str, pos_group: str) -> dict[str, object]:
    return {
        "canonical_key": canonical_key,
        "pos_group": pos_group,
    }


def support_row(sentence: str, covered_keys: str) -> dict[str, object]:
    return {
        "cleaned_sentence": sentence,
        "cleaned_covered_canonical_keys": covered_keys,
    }


def indexed_candidate(
    row_id: str,
    dictionary_entry: dict[str, object],
    term_row: dict[str, object],
    support_sentences: list[str],
) -> IndexedDictionaryCandidate:
    return IndexedDictionaryCandidate(
        row_id=row_id,
        candidate=dictionary_entry,
        prompt_packet={
            "canonical_key": dictionary_entry["canonical_key"],
            "display": dictionary_entry["display"],
            "pos_group": term_row["pos_group"],
            "count": dictionary_entry["count"],
            "document_count": dictionary_entry["document_count"],
            "quality_score": dictionary_entry["quality_score"],
            "roi_score": dictionary_entry["roi_score"],
            "variants": dictionary_entry["variants"],
            "source_example": dictionary_entry["example"],
            "support_sentences": support_sentences,
        },
    )


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


class PromptCapturingCompletionClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[list[object]] = []

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        self.messages.append(list(messages))
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=self.content,
        )


if __name__ == "__main__":
    unittest.main()
