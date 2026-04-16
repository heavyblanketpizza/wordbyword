from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.pipeline import run_pipeline


class PipelineTest(unittest.TestCase):
    def test_extracts_korean_tokens_and_expression_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "sample.txt").write_text(
                "● 1 2 한국어 표현을\u2003공부하고 있다. ●\n"
                "1 2 한국어 책을 읽고 있다.\n"
                "칭찬할 점 친구 이름 알맞은 말.",
                encoding="utf-8",
            )

            result = run_pipeline(input_dir=input_dir, output_dir=output_dir)

            self.assertEqual(result.documents, 1)
            self.assertGreater(result.token_occurrences, 0)
            self.assertTrue((output_dir / "tokens.csv").exists())
            self.assertTrue((output_dir / "vocabulary.csv").exists())
            self.assertTrue((output_dir / "expressions.csv").exists())
            self.assertTrue((output_dir / "candidate_terms.csv").exists())
            self.assertTrue((output_dir / "candidate_expressions.csv").exists())
            self.assertTrue((output_dir / "rejected_candidates.csv").exists())
            self.assertTrue((output_dir / "dictionary_candidates.jsonl").exists())

            with (output_dir / "vocabulary.csv").open(encoding="utf-8-sig") as handle:
                vocabulary = list(csv.DictReader(handle))
            self.assertIn("한국어", {row["term"] for row in vocabulary})
            examples = [row["example"] for row in vocabulary]
            self.assertTrue(all("\n" not in example for example in examples))
            self.assertTrue(all("\u2003" not in example for example in examples))
            self.assertTrue(all("●" not in example for example in examples))
            self.assertNotIn("●", {row["term"] for row in vocabulary})

            with (output_dir / "expressions.csv").open(encoding="utf-8-sig") as handle:
                expressions = list(csv.DictReader(handle))
            self.assertIn("한국어 표현을", {row["expression"] for row in expressions})
            self.assertNotIn("1 2", {row["expression"] for row in expressions})

            with (output_dir / "candidate_terms.csv").open(encoding="utf-8-sig") as handle:
                candidate_terms = list(csv.DictReader(handle))
            term_by_key = {row["canonical_key"]: row for row in candidate_terms}
            self.assertIn("공부하다", term_by_key)
            self.assertIn("공부하고", term_by_key["공부하다"]["variants"])

            with (output_dir / "rejected_candidates.csv").open(encoding="utf-8-sig") as handle:
                rejected = list(csv.DictReader(handle))
            rejected_by_candidate = {row["candidate"]: row["reason"] for row in rejected}
            self.assertEqual(rejected_by_candidate["점 친구 이름"], "table_header_fragment")

            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["document_count"], 1)
            self.assertGreater(summary["clean_candidate_terms"], 0)
            self.assertGreater(summary["clean_candidate_expressions"], 0)
            for path in output_dir.iterdir():
                self.assertNotIn("●", path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
