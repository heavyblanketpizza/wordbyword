from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.pipeline import run_pipeline
from wordbyword.writers import parsed_document_count


class PipelineTest(unittest.TestCase):
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
            self.assertNotIn("filtered_example_sentence.csv", summary["outputs"])
            self.assertNotIn("06_expressions_raw.csv", summary["outputs"])
            self.assertNotIn("08_candidate_expressions.csv", summary["outputs"])
            self.assertIn("pipeline_stages", summary)
            self.assertGreater(summary["clean_candidate_terms"], 0)
            self.assertEqual(summary["example_sentences"], len(example_sentences))
            self.assertNotIn("filtered_example_sentences", summary)
            self.assertNotIn("clean_candidate_expressions", summary)
            for path in output_dir.iterdir():
                output_text = path.read_text(encoding="utf-8-sig")
                self.assertNotIn("first_source", output_text)
                self.assertNotIn("first_page", output_text)
                self.assertNotIn("●", output_text)


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


if __name__ == "__main__":
    unittest.main()
