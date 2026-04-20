from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.pipeline import run_pipeline


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
            self.assertTrue((output_dir / "README.md").exists())
            self.assertFalse((output_dir / "tokens.csv").exists())
            self.assertFalse((output_dir / "expressions.csv").exists())
            self.assertFalse((output_dir / "06_expressions_raw.csv").exists())
            self.assertFalse((output_dir / "08_candidate_expressions.csv").exists())

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
            self.assertIn("한국어", {row["term"] for row in vocabulary})
            examples = [row["example"] for row in vocabulary]
            self.assertTrue(all("\n" not in example for example in examples))
            self.assertTrue(all("\u2003" not in example for example in examples))
            self.assertTrue(all("●" not in example for example in examples))
            self.assertNotIn("●", {row["term"] for row in vocabulary})

            with (output_dir / "06_candidate_terms.csv").open(encoding="utf-8-sig") as handle:
                candidate_terms = list(csv.DictReader(handle))
            term_by_key = {row["canonical_key"]: row for row in candidate_terms}
            self.assertIn("공부하다", term_by_key)
            self.assertIn("공부하고", term_by_key["공부하다"]["variants"])
            self.assertIn("귤", term_by_key)

            with (output_dir / "07_rejected_terms.csv").open(encoding="utf-8-sig") as handle:
                rejected = list(csv.DictReader(handle))
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

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["document_count"], 1)
            self.assertEqual(summary["extracted_page_count"], len(pages))
            self.assertEqual(summary["sentence_count"], len(sentences))
            self.assertIn("01_extracted_pages.csv", summary["outputs"])
            self.assertIn("02_sentences.csv", summary["outputs"])
            self.assertNotIn("06_expressions_raw.csv", summary["outputs"])
            self.assertNotIn("08_candidate_expressions.csv", summary["outputs"])
            self.assertIn("pipeline_stages", summary)
            self.assertGreater(summary["clean_candidate_terms"], 0)
            self.assertNotIn("clean_candidate_expressions", summary)
            for path in output_dir.iterdir():
                self.assertNotIn("●", path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
