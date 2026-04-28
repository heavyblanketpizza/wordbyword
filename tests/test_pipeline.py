from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.llm import CompletionResult
from wordbyword.pipeline import PipelineUsageError, run_pipeline
from wordbyword.writers import parsed_document_count


class PipelineTest(unittest.TestCase):
    def test_pipeline_reports_major_run_phases(self) -> None:
        phases: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "sample.txt").write_text("한국어 책을 읽고 있다.", encoding="utf-8")

            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                progress=lambda phase, detail: phases.append((phase, detail)),
            )

        self.assertEqual([phase for phase, _ in phases], ["scan", "extract", "analyze", "write"])
        self.assertTrue(all(detail for _, detail in phases))

    def test_extracts_korean_tokens_and_vocabulary_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (output_dir / "tokens.csv").write_text("old output", encoding="utf-8")
            (output_dir / "expressions.csv").write_text("old output", encoding="utf-8")
            (output_dir / "06_expressions_raw.csv").write_text("old output", encoding="utf-8")
            (output_dir / "08_candidate_expressions.csv").write_text("old output", encoding="utf-8")
            (output_dir / "filtered_example_sentence.csv").write_text("old output", encoding="utf-8")
            (input_dir / "sample.txt").write_text(
                "● 1 2 한국어 표현을\u2003공부하고 있다. ●\n"
                "?\n\n"
                "가\n\n"
                "146\n\n"
                "1 2 한국어 책을 읽고 있다.\n"
                "칭찬할 점 친구 이름 알맞은 말.\n"
                "귤을 먹었다.",
                encoding="utf-8",
            )

            result = run_pipeline(input_dir=input_dir, output_dir=output_dir)

            self.assertEqual(result.documents, 1)
            self.assertEqual(result.extracted_pages, 1)
            self.assertGreater(result.sentences, 0)
            self.assertGreater(result.token_occurrences, 0)
            self.assertTrue((output_dir / "00_summary.json").exists())
            self.assertTrue((output_dir / "01_extracted_pages.csv").exists())
            self.assertTrue((output_dir / "02_sentences.csv").exists())
            self.assertTrue((output_dir / "03_tokens.csv").exists())
            self.assertTrue((output_dir / "04_surface_words.csv").exists())
            self.assertTrue((output_dir / "05_vocabulary_raw.csv").exists())
            self.assertTrue((output_dir / "06_candidate_terms.csv").exists())
            self.assertTrue((output_dir / "07_rejected_terms.csv").exists())
            self.assertTrue((output_dir / "08_dictionary_candidates.jsonl").exists())
            self.assertTrue((output_dir / "09_example_sentences.csv").exists())
            self.assertFalse((output_dir / "10_cleaned_example_sentences.csv").exists())
            self.assertTrue((output_dir / "README.md").exists())
            self.assertFalse((output_dir / "tokens.csv").exists())
            self.assertFalse((output_dir / "expressions.csv").exists())
            self.assertFalse((output_dir / "06_expressions_raw.csv").exists())
            self.assertFalse((output_dir / "08_candidate_expressions.csv").exists())
            self.assertFalse((output_dir / "filtered_example_sentence.csv").exists())

            with (output_dir / "01_extracted_pages.csv").open(encoding="utf-8-sig") as handle:
                pages = list(csv.DictReader(handle))
            self.assertEqual(
                list(pages[0].keys()),
                ["document", "page", "raw_character_count", "clean_character_count", "text"],
            )
            self.assertEqual(len(pages), 1)
            self.assertIn("한국어 표현을 공부하고 있다.", pages[0]["text"])
            self.assertNotIn("●", pages[0]["text"])

            with (output_dir / "02_sentences.csv").open(encoding="utf-8-sig") as handle:
                sentences = list(csv.DictReader(handle))
            self.assertEqual(
                list(sentences[0].keys()),
                ["document", "page", "sentence_index", "sentence"],
            )
            sentence_texts = [row["sentence"] for row in sentences]
            self.assertIn("1 2 한국어 표현을 공부하고 있다.", sentence_texts)
            self.assertIn("칭찬할 점 친구 이름 알맞은 말.", sentence_texts)
            self.assertNotIn("?", sentence_texts)
            self.assertNotIn("가", sentence_texts)
            self.assertNotIn("146", sentence_texts)
            self.assertTrue(all("\n" not in sentence for sentence in sentence_texts))
            self.assertTrue(all("\u2003" not in sentence for sentence in sentence_texts))
            self.assertTrue(all("●" not in sentence for sentence in sentence_texts))

            with (output_dir / "05_vocabulary_raw.csv").open(encoding="utf-8-sig") as handle:
                vocabulary = list(csv.DictReader(handle))
            self.assertNotIn("first_source", vocabulary[0])
            self.assertNotIn("first_page", vocabulary[0])
            self.assertIn("한국어", {row["term"] for row in vocabulary})
            examples = [row["example"] for row in vocabulary]
            self.assertTrue(all("\n" not in example for example in examples))
            self.assertTrue(all("\u2003" not in example for example in examples))
            self.assertTrue(all("●" not in example for example in examples))
            self.assertNotIn("●", {row["term"] for row in vocabulary})

            with (output_dir / "04_surface_words.csv").open(encoding="utf-8-sig") as handle:
                surface_words = list(csv.DictReader(handle))
            self.assertNotIn("first_source", surface_words[0])
            self.assertNotIn("first_page", surface_words[0])

            with (output_dir / "06_candidate_terms.csv").open(encoding="utf-8-sig") as handle:
                candidate_terms = list(csv.DictReader(handle))
            self.assertNotIn("first_source", candidate_terms[0])
            self.assertNotIn("first_page", candidate_terms[0])
            term_by_key = {row["canonical_key"]: row for row in candidate_terms}
            self.assertIn("공부하다", term_by_key)
            self.assertIn("공부하고", term_by_key["공부하다"]["variants"])
            self.assertIn("귤", term_by_key)

            with (output_dir / "07_rejected_terms.csv").open(encoding="utf-8-sig") as handle:
                rejected = list(csv.DictReader(handle))
            self.assertTrue(all("first_source" not in row for row in rejected))
            self.assertTrue(all("first_page" not in row for row in rejected))
            self.assertTrue(all(row["kind"] == "term" for row in rejected))
            self.assertNotIn("single_character_low_frequency", {row["reason"] for row in rejected})

            dictionary_rows = [
                json.loads(line)
                for line in (output_dir / "08_dictionary_candidates.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(dictionary_rows)
            self.assertTrue(all(row["kind"] == "term" for row in dictionary_rows))
            self.assertTrue(all("first_source" not in row for row in dictionary_rows))
            self.assertTrue(all("first_page" not in row for row in dictionary_rows))

            with (output_dir / "09_example_sentences.csv").open(encoding="utf-8-sig") as handle:
                example_sentences = list(csv.DictReader(handle))
            self.assertTrue(example_sentences)
            self.assertLessEqual(len(example_sentences), len(sentences))
            self.assertEqual(
                list(example_sentences[0].keys()),
                [
                    "document",
                    "page",
                    "sentence_index",
                    "sentence",
                    "quality_score",
                    "matched_canonical_key_count",
                    "covered_canonical_key_count",
                    "covered_canonical_keys",
                    "matched_canonical_keys",
                ],
            )
            matched_counts = [int(row["matched_canonical_key_count"]) for row in example_sentences]
            covered_counts = [int(row["covered_canonical_key_count"]) for row in example_sentences]
            quality_scores = [float(row["quality_score"]) for row in example_sentences]
            quality_density_scores = [
                float(row["quality_score"]) / int(row["matched_canonical_key_count"])
                for row in example_sentences
            ]
            matched_key_sets = [row["matched_canonical_keys"] for row in example_sentences]
            self.assertEqual(len(matched_key_sets), len(set(matched_key_sets)))
            self.assertTrue(all(5 <= covered <= 15 for covered in covered_counts))
            self.assertTrue(all(matched >= covered for matched, covered in zip(matched_counts, covered_counts)))
            self.assertTrue(all(0 <= score <= 1 for score in quality_scores))
            self.assertEqual(max(quality_scores), 1.0)
            self.assertEqual(quality_density_scores, sorted(quality_density_scores, reverse=True))
            for row in example_sentences:
                density = float(row["quality_score"]) / int(row["matched_canonical_key_count"])
                sentence = row["sentence"].strip()
                self.assertGreaterEqual(density, 0.0135)
                self.assertTrue(sentence.endswith((".", "?", "!", '"')))
                self.assertFalse(sentence.rstrip('"').strip().endswith("봅시다."))

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["document_count"], 1)
            self.assertEqual(summary["extracted_page_count"], len(pages))
            self.assertEqual(summary["sentence_count"], len(sentences))
            self.assertIn("01_extracted_pages.csv", summary["outputs"])
            self.assertIn("02_sentences.csv", summary["outputs"])
            self.assertIn("09_example_sentences.csv", summary["outputs"])
            self.assertNotIn("10_cleaned_example_sentences.csv", summary["outputs"])
            self.assertNotIn("filtered_example_sentence.csv", summary["outputs"])
            self.assertNotIn("06_expressions_raw.csv", summary["outputs"])
            self.assertNotIn("08_candidate_expressions.csv", summary["outputs"])
            self.assertIn("pipeline_stages", summary)
            self.assertGreater(summary["clean_candidate_terms"], 0)
            self.assertEqual(summary["example_sentences"], len(example_sentences))
            self.assertNotIn("cleaned_example_sentences", summary)
            self.assertNotIn("filtered_example_sentences", summary)
            self.assertNotIn("clean_candidate_expressions", summary)
            for path in output_dir.iterdir():
                output_text = path.read_text(encoding="utf-8-sig")
                self.assertNotIn("first_source", output_text)
                self.assertNotIn("first_page", output_text)
                self.assertNotIn("●", output_text)

    def test_pipeline_can_write_cleaned_example_sentences_with_stub_client(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "sample.txt").write_text(
                "한국어 표현을 공부하고 있다. 한국어 책을 읽고 있다. 점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다.",
                encoding="utf-8",
            )

            run_pipeline(input_dir=input_dir, output_dir=output_dir)

            (input_dir / "sample.txt").write_text(
                "이 문장은 cleanup-only run에서 읽히면 안 된다.",
                encoding="utf-8",
            )

            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                clean_example_sentences=True,
                sentence_cleanup_client=AlwaysKeepCleanupClient(),
            )

            self.assertTrue((output_dir / "10_cleaned_example_sentences.csv").exists())

            with (output_dir / "09_example_sentences.csv").open(encoding="utf-8-sig") as handle:
                example_sentences = list(csv.DictReader(handle))
            with (output_dir / "10_cleaned_example_sentences.csv").open(encoding="utf-8-sig") as handle:
                cleaned_example_sentences = list(csv.DictReader(handle))

            self.assertEqual(len(cleaned_example_sentences), len(example_sentences))
            self.assertEqual(
                list(cleaned_example_sentences[0].keys()),
                [
                    "document",
                    "page",
                    "sentence_index",
                    "source_sentence",
                    "cleaned_sentence",
                    "cleanup_action",
                    "cleanup_reason",
                    "model_confidence",
                    "source_similarity",
                    "source_quality_score",
                    "source_matched_canonical_key_count",
                    "source_covered_canonical_key_count",
                    "source_covered_canonical_keys",
                    "source_matched_canonical_keys",
                    "cleaned_quality_score",
                    "cleaned_matched_canonical_key_count",
                    "cleaned_covered_canonical_key_count",
                    "cleaned_covered_canonical_keys",
                    "cleaned_matched_canonical_keys",
                ],
            )
            self.assertTrue(all(row["cleanup_action"] == "keep" for row in cleaned_example_sentences))
            self.assertTrue(all(row["source_sentence"] == row["cleaned_sentence"] for row in cleaned_example_sentences))
            self.assertTrue(all(float(row["source_similarity"]) == 1.0 for row in cleaned_example_sentences))

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("10_cleaned_example_sentences.csv", summary["outputs"])
            self.assertEqual(summary["reviewed_example_sentences"], len(example_sentences))
            self.assertEqual(summary["cleaned_example_sentences"], len(cleaned_example_sentences))
            self.assertEqual(summary["rejected_cleaned_example_sentences"], 0)
            self.assertEqual(summary["cleaned_example_sentence_actions"]["keep"], len(cleaned_example_sentences))
            self.assertEqual(summary["cleaned_example_sentence_actions"]["reject"], 0)
            self.assertIn("점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다.", {
                row["source_sentence"] for row in cleaned_example_sentences
            })
            self.assertNotIn("이 문장은 cleanup-only run에서 읽히면 안 된다.", {
                row["source_sentence"] for row in cleaned_example_sentences
            })

    def test_pipeline_can_write_cleaned_dictionary_candidates_with_stub_client(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "sample.txt").write_text(
                "점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다. "
                "이가 아픈 여우가 드소토 선생님을 찾아왔다. "
                "민속 박물관에 있는 옛날 집 안의 모습입니다.",
                encoding="utf-8",
            )

            run_pipeline(input_dir=input_dir, output_dir=output_dir)

            (input_dir / "sample.txt").write_text(
                "이 텍스트는 cleanup-only dictionary run에서 다시 분석되면 안 된다.",
                encoding="utf-8",
            )

            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                clean_dictionary_candidates=True,
                dictionary_cleanup_client=AlwaysKeepCleanupClient(),
            )

            self.assertTrue((output_dir / "11_cleaned_dictionary_candidates.jsonl").exists())
            self.assertFalse((output_dir / "10_cleaned_example_sentences.csv").exists())

            dictionary_rows = [
                json.loads(line)
                for line in (output_dir / "08_dictionary_candidates.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            cleaned_dictionary_rows = [
                json.loads(line)
                for line in (output_dir / "11_cleaned_dictionary_candidates.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(len(cleaned_dictionary_rows), len(dictionary_rows))
            self.assertEqual(
                [row["canonical_key"] for row in cleaned_dictionary_rows],
                [row["canonical_key"] for row in dictionary_rows],
            )

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("11_cleaned_dictionary_candidates.jsonl", summary["outputs"])
            self.assertEqual(summary["reviewed_dictionary_candidates"], len(dictionary_rows))
            self.assertEqual(summary["cleaned_dictionary_candidates"], len(cleaned_dictionary_rows))
            self.assertEqual(summary["rejected_cleaned_dictionary_candidates"], 0)
            self.assertEqual(summary["cleaned_dictionary_candidate_actions"]["keep"], len(cleaned_dictionary_rows))
            self.assertEqual(summary["cleaned_dictionary_candidate_actions"]["reject"], 0)
            self.assertIn("여우", {row["canonical_key"] for row in cleaned_dictionary_rows})

    def test_pipeline_builds_stage_twelve_after_saved_stage_ten_and_eleven_exist(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "sample.txt").write_text(
                "점심을 먹은 뒤에 어머니와 함께 놀이터로 나갔다. "
                "민속 박물관에 있는 옛날 집 안의 모습입니다. "
                "아저씨 집에서 달콤한 딸기 냄새가 나요.",
                encoding="utf-8",
            )

            run_pipeline(input_dir=input_dir, output_dir=output_dir)
            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                clean_example_sentences=True,
                sentence_cleanup_client=AlwaysKeepCleanupClient(),
            )
            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                clean_dictionary_candidates=True,
                dictionary_cleanup_client=AlwaysKeepCleanupClient(),
            )

            self.assertTrue((output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").exists())

            linked_rows = [
                json.loads(line)
                for line in (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

            self.assertTrue(linked_rows)
            self.assertTrue(all("example_replaced_with_cleaned_sentence" in row for row in linked_rows))
            self.assertTrue(any(bool(row["example_replaced_with_cleaned_sentence"]) for row in linked_rows))
            self.assertTrue(all(row.get("curation_status") == "rewrite_ready" for row in linked_rows))
            self.assertTrue(
                all(
                    bool(str(row["example"]).strip())
                    for row in linked_rows
                    if bool(row["example_replaced_with_cleaned_sentence"])
                )
            )
            self.assertTrue(all("original_example" not in row for row in linked_rows))
            self.assertTrue(all("cleaned_example_document" not in row for row in linked_rows))

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("12_dictionary_candidates_with_cleaned_examples.jsonl", summary["outputs"])
            self.assertEqual(summary["linked_dictionary_candidates"], len(linked_rows))
            self.assertGreater(summary["linked_cleaned_example_sentences"], 0)

    def test_pipeline_can_generate_missing_examples_from_saved_stage_twelve(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            with (output_dir / "06_candidate_terms.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "canonical_key",
                        "display",
                        "kind",
                        "pos_group",
                        "count",
                        "surface_count",
                        "document_count",
                        "variant_count",
                        "variants",
                        "quality_score",
                        "roi_score",
                        "example",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "canonical_key": "학교",
                            "display": "학교",
                            "kind": "term",
                            "pos_group": "noun",
                            "count": 10,
                            "surface_count": 10,
                            "document_count": 1,
                            "variant_count": 1,
                            "variants": "학교",
                            "quality_score": 0.9,
                            "roi_score": 0.5,
                            "example": "학교 예문",
                        },
                        {
                            "canonical_key": "버스",
                            "display": "버스",
                            "kind": "term",
                            "pos_group": "noun",
                            "count": 9,
                            "surface_count": 9,
                            "document_count": 1,
                            "variant_count": 1,
                            "variants": "버스",
                            "quality_score": 0.8,
                            "roi_score": 0.5,
                            "example": "버스 예문",
                        },
                    ]
                )

            (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "canonical_key": "학교",
                                "display": "학교",
                                "kind": "term",
                                "variants": ["학교"],
                                "count": 10,
                                "document_count": 1,
                                "quality_score": 0.9,
                                "roi_score": 0.5,
                                "example": "원래 학교 예문이다.",
                                "curation_status": "rewrite_ready",
                                "example_replaced_with_cleaned_sentence": False,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "canonical_key": "버스",
                                "display": "버스",
                                "kind": "term",
                                "variants": ["버스"],
                                "count": 9,
                                "document_count": 1,
                                "quality_score": 0.8,
                                "roi_score": 0.5,
                                "example": "아침마다 버스를 타고 회사에 간다.",
                                "curation_status": "rewrite_ready",
                                "example_replaced_with_cleaned_sentence": True,
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")

            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                generate_missing_examples=True,
                generated_example_client=GeneratedExampleClient(),
            )

            self.assertTrue((output_dir / "13_dictionary_candidates_with_generated_examples.jsonl").exists())
            self.assertTrue((output_dir / "13_generated_example_attempts.jsonl").exists())
            self.assertTrue((output_dir / "13_generated_example_failures.jsonl").exists())
            generated_rows = [
                json.loads(line)
                for line in (output_dir / "13_dictionary_candidates_with_generated_examples.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            generated_attempt_rows = [
                json.loads(line)
                for line in (output_dir / "13_generated_example_attempts.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

            self.assertEqual(len(generated_rows), 2)
            self.assertEqual(len(generated_attempt_rows), 1)
            self.assertEqual(generated_rows[0]["example"], "학생들이 학교 운동장에서 축구를 한다.")
            self.assertTrue(generated_rows[0]["example_generated_with_ollama"])
            self.assertFalse(generated_rows[0]["example_replaced_with_cleaned_sentence"])
            self.assertEqual(generated_rows[1]["example"], "아침마다 버스를 타고 회사에 간다.")
            self.assertFalse(generated_rows[1]["example_generated_with_ollama"])
            self.assertTrue(generated_rows[1]["example_replaced_with_cleaned_sentence"])
            self.assertEqual(generated_attempt_rows[0]["canonical_key"], "학교")
            self.assertTrue(generated_attempt_rows[0]["accepted"])
            self.assertEqual(
                (output_dir / "13_generated_example_failures.jsonl").read_text(encoding="utf-8"),
                "",
            )

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("13_dictionary_candidates_with_generated_examples.jsonl", summary["outputs"])
            self.assertIn("13_generated_example_attempts.jsonl", summary["outputs"])
            self.assertIn("13_generated_example_failures.jsonl", summary["outputs"])
            self.assertEqual(summary["reviewed_missing_dictionary_examples"], 1)
            self.assertEqual(summary["generated_dictionary_candidate_examples"], 1)
            self.assertEqual(summary["preserved_dictionary_candidate_examples"], 1)
            self.assertEqual(summary["failed_dictionary_candidate_examples"], 0)
            self.assertEqual(summary["generated_example_attempt_rounds"], 1)
            self.assertIn("13", {stage["stage"] for stage in summary["pipeline_stages"]})

    def test_pipeline_can_retry_failed_generated_examples_and_checkpoint_repair_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            with (output_dir / "06_candidate_terms.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "canonical_key",
                        "display",
                        "kind",
                        "pos_group",
                        "count",
                        "surface_count",
                        "document_count",
                        "variant_count",
                        "variants",
                        "quality_score",
                        "roi_score",
                        "example",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "canonical_key": "비슷하다",
                        "display": "비슷하다",
                        "kind": "term",
                        "pos_group": "adjective",
                        "count": 10,
                        "surface_count": 10,
                        "document_count": 1,
                        "variant_count": 1,
                        "variants": "비슷하다",
                        "quality_score": 0.9,
                        "roi_score": 0.5,
                        "example": "비슷하다 예문",
                    }
                )

            (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").write_text(
                json.dumps(
                    {
                        "canonical_key": "비슷하다",
                        "display": "비슷하다",
                        "kind": "term",
                        "variants": ["비슷하다"],
                        "count": 10,
                        "document_count": 1,
                        "quality_score": 0.9,
                        "roi_score": 0.5,
                        "example": "비슷하다",
                        "curation_status": "rewrite_ready",
                        "example_replaced_with_cleaned_sentence": False,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")

            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                generate_missing_examples=True,
                generated_example_client=RetryingGeneratedExampleClient(),
                generated_example_retry_decider=lambda summary: summary.failed_count > 0,
            )

            generated_rows = [
                json.loads(line)
                for line in (output_dir / "13_dictionary_candidates_with_generated_examples.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            generated_attempt_rows = [
                json.loads(line)
                for line in (output_dir / "13_generated_example_attempts.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

            self.assertEqual(generated_rows[0]["example"], "두 그림은 서로 닮은 점이 많다.")
            self.assertTrue(generated_rows[0]["example_generated_with_ollama"])
            self.assertEqual(len(generated_attempt_rows), 2)
            self.assertFalse(generated_attempt_rows[0]["accepted"])
            self.assertTrue(generated_attempt_rows[1]["accepted"])
            self.assertEqual(generated_attempt_rows[1]["attempt_kind"], "repair")
            self.assertEqual(generated_attempt_rows[1]["review_action"], "keep_generated")
            self.assertEqual(
                generated_attempt_rows[1]["input_generated_sentence"],
                "두 그림은 서로 닮은 점이 많다.",
            )
            self.assertEqual(
                (output_dir / "13_generated_example_failures.jsonl").read_text(encoding="utf-8"),
                "",
            )

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["generated_example_attempt_rounds"], 2)
            self.assertEqual(summary["failed_dictionary_candidate_examples"], 0)

    def test_pipeline_checkpoints_generated_example_output_after_each_batch(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            with (output_dir / "06_candidate_terms.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "canonical_key",
                        "display",
                        "kind",
                        "pos_group",
                        "count",
                        "surface_count",
                        "document_count",
                        "variant_count",
                        "variants",
                        "quality_score",
                        "roi_score",
                        "example",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "canonical_key": "사과",
                        "display": "사과",
                        "kind": "term",
                        "pos_group": "noun",
                        "count": 26,
                        "surface_count": 26,
                        "document_count": 1,
                        "variant_count": 1,
                        "variants": "사과",
                        "quality_score": 0.9,
                        "roi_score": 0.5,
                        "example": "사과 예문",
                    }
                )

            (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "canonical_key": "사과",
                            "display": f"사과-{index}",
                            "kind": "term",
                            "variants": ["사과"],
                            "count": 1,
                            "document_count": 1,
                            "quality_score": 0.9,
                            "roi_score": 0.5,
                            "example": f"원래 사과 예문 {index}.",
                            "curation_status": "rewrite_ready",
                            "example_replaced_with_cleaned_sentence": False,
                        },
                        ensure_ascii=False,
                    )
                    for index in range(41)
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")

            client = CheckpointingGeneratedExampleClient(output_dir)
            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                generate_missing_examples=True,
                generated_example_client=client,
            )

            self.assertEqual(client.batch_sizes, [40, 1])
            self.assertEqual(client.checkpoint_generated_count_after_first_batch, 40)

    def test_example_cleanup_requires_saved_stage_nine_and_stage_six(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "sample.txt").write_text("한국어 책을 읽고 있다.", encoding="utf-8")

            with self.assertRaises(PipelineUsageError):
                run_pipeline(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    clean_example_sentences=True,
                    sentence_cleanup_client=AlwaysKeepCleanupClient(),
                )

    def test_dictionary_cleanup_requires_saved_stage_eight_and_support_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "sample.txt").write_text("한국어 책을 읽고 있다.", encoding="utf-8")

            with self.assertRaises(PipelineUsageError):
                run_pipeline(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    clean_dictionary_candidates=True,
                    dictionary_cleanup_client=AlwaysKeepCleanupClient(),
                )

    def test_same_basename_in_different_subdirs_stays_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            (input_dir / "grade2" / "unit1").mkdir(parents=True)
            (input_dir / "mock-exams" / "unit1").mkdir(parents=True)
            (input_dir / "grade2" / "unit1" / "lesson.txt").write_text(
                "사과 친구 학교 책 연필을 먹었다. 공부를 했다.",
                encoding="utf-8",
            )
            (input_dir / "mock-exams" / "unit1" / "lesson.txt").write_text(
                "바나나 친구 학교 책 연필을 먹었다. 공부를 했다.",
                encoding="utf-8",
            )

            result = run_pipeline(input_dir=input_dir, output_dir=output_dir)

            self.assertEqual(result.documents, 2)

            grade_id = "grade2/unit1/lesson.txt"
            mock_id = "mock-exams/unit1/lesson.txt"

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["document_count"], 2)
            self.assertEqual(set(summary["tokens_by_document"].keys()), {grade_id, mock_id})
            self.assertGreater(summary["tokens_by_document"][grade_id], 0)
            self.assertGreater(summary["tokens_by_document"][mock_id], 0)

            with (output_dir / "01_extracted_pages.csv").open(encoding="utf-8-sig") as handle:
                pages = list(csv.DictReader(handle))
            self.assertEqual({row["document"] for row in pages}, {grade_id, mock_id})

            with (output_dir / "02_sentences.csv").open(encoding="utf-8-sig") as handle:
                sentences = list(csv.DictReader(handle))
            self.assertEqual({row["document"] for row in sentences}, {grade_id, mock_id})

            with (output_dir / "03_tokens.csv").open(encoding="utf-8-sig") as handle:
                tokens = list(csv.DictReader(handle))
            self.assertEqual({row["document"] for row in tokens}, {grade_id, mock_id})

            with (output_dir / "09_example_sentences.csv").open(encoding="utf-8-sig") as handle:
                example_sentences = list(csv.DictReader(handle))
            self.assertEqual({row["document"] for row in example_sentences}, {grade_id, mock_id})

            with (output_dir / "06_candidate_terms.csv").open(encoding="utf-8-sig") as handle:
                candidate_terms = list(csv.DictReader(handle))
            term_by_key = {row["canonical_key"]: row for row in candidate_terms}
            # Shared term appears in both files → document_count must be 2, not 1.
            self.assertIn("먹다", term_by_key)
            self.assertEqual(int(term_by_key["먹다"]["document_count"]), 2)
            # Unique terms stay pinned to their single source.
            self.assertIn("사과", term_by_key)
            self.assertEqual(int(term_by_key["사과"]["document_count"]), 1)
            self.assertIn("바나나", term_by_key)
            self.assertEqual(int(term_by_key["바나나"]["document_count"]), 1)

            # Join integrity: a sentence row's (document, page, sentence_index) must only
            # bind to tokens from the same file, even though both share sentence_index=1.
            grade_first = next(
                row for row in sentences
                if row["document"] == grade_id and row["sentence_index"] == "1"
            )
            mock_first = next(
                row for row in sentences
                if row["document"] == mock_id and row["sentence_index"] == "1"
            )
            grade_tokens = {
                token["form"] for token in tokens
                if token["document"] == grade_id and token["sentence_index"] == "1"
            }
            mock_tokens = {
                token["form"] for token in tokens
                if token["document"] == mock_id and token["sentence_index"] == "1"
            }
            self.assertIn("사과", grade_tokens)
            self.assertNotIn("바나나", grade_tokens)
            self.assertIn("바나나", mock_tokens)
            self.assertNotIn("사과", mock_tokens)
            self.assertIn("사과", grade_first["sentence"])
            self.assertIn("바나나", mock_first["sentence"])

            grade_example = next(
                row for row in example_sentences
                if row["document"] == grade_id and row["sentence_index"] == "1"
            )
            mock_example = next(
                row for row in example_sentences
                if row["document"] == mock_id and row["sentence_index"] == "1"
            )
            self.assertIn("사과", grade_example["matched_canonical_keys"])
            self.assertNotIn("바나나", grade_example["matched_canonical_keys"])
            self.assertIn("바나나", mock_example["matched_canonical_keys"])
            self.assertNotIn("사과", mock_example["matched_canonical_keys"])

    def test_parsed_document_count_fallback_uses_full_document_list(self) -> None:
        documents = [
            Path("/tmp/original/grade2/unit1/lesson.txt"),
            Path("/tmp/original/mock-exams/unit1/lesson.txt"),
        ]

        self.assertEqual(parsed_document_count([], documents), 2)


class AlwaysKeepCleanupClient:
    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="EMPTY",
        )


class GeneratedExampleClient:
    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        lines = []
        for row in rows:
            assert set(row) == {"id", "headword", "variants"}
            if row["headword"] == "학교":
                sentence = "학생들이 학교 운동장에서 축구를 한다."
            else:
                sentence = "아침마다 버스를 타고 회사에 간다."
            lines.append(f"{row['id']}\t{sentence}")
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines),
        )


class CheckpointingGeneratedExampleClient:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.batch_sizes: list[int] = []
        self.checkpoint_generated_count_after_first_batch: int | None = None

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        self.batch_sizes.append(len(rows))
        if len(self.batch_sizes) == 2:
            generated_rows = [
                json.loads(line)
                for line in (
                    self.output_dir / "13_dictionary_candidates_with_generated_examples.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.checkpoint_generated_count_after_first_batch = sum(
                1 for row in generated_rows if bool(row["example_generated_with_ollama"])
            )
        lines = [f"{row['id']}\t학생 {row['id'][1:]}이 사과를 먹는다." for row in rows]
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines),
        )


class RetryingGeneratedExampleClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        self.calls += 1
        row = json.loads(messages[-1].content.splitlines()[1])
        if self.calls == 1:
            assert set(row) == {"id", "headword", "variants"}
            content = f"{row['id']}\t두 그림은 서로 닮은 점이 많다."
        else:
            assert set(row) == {
                "id",
                "headword",
                "variants",
                "source_example",
                "generated_example",
                "failure_reason",
            }
            content = f"{row['id']}\tkeep_generated\t"
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content=content,
        )


if __name__ == "__main__":
    unittest.main()
