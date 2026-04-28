from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wordbyword.cli import (
    link_cleaned_examples,
    load_dictionary_candidates,
    main,
    prompt_generated_example_retry,
    resolve_distribution_path,
    show_distribution,
)
from wordbyword.example_generation import GeneratedExampleAttemptSummary
from wordbyword.pipeline import PipelineResult


class ShowDistributionCliTest(unittest.TestCase):
    def test_resolve_distribution_path_uses_output_default(self) -> None:
        path = resolve_distribution_path("", Path("output"))
        self.assertEqual(path, Path("output") / "11_cleaned_dictionary_candidates.jsonl")

    def test_resolve_distribution_path_uses_supplied_value(self) -> None:
        path = resolve_distribution_path("/tmp/relics.jsonl", Path("output"))
        self.assertEqual(path, Path("/tmp/relics.jsonl"))

    def test_load_dictionary_candidates_reads_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "11_cleaned_dictionary_candidates.jsonl"
            rows = [
                {"canonical_key": "가다", "quality_score": 0.42, "count": 12},
                {"canonical_key": "오다", "quality_score": 0.71, "count": 88},
            ]
            jsonl_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            entries = load_dictionary_candidates(jsonl_path)

            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["canonical_key"], "가다")
            self.assertEqual(entries[1]["count"], 88)

    def test_load_dictionary_candidates_raises_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "broken.jsonl"
            jsonl_path.write_text("{not json}\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_dictionary_candidates(jsonl_path)

    def test_show_distribution_prints_panel_for_existing_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "11_cleaned_dictionary_candidates.jsonl"
            rows = [
                {"quality_score": 0.12, "count": 3},
                {"quality_score": 0.46, "count": 18},
                {"quality_score": 0.91, "count": 220},
            ]
            jsonl_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = show_distribution(jsonl_path=jsonl_path, model="gemma4:31b")

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("RELIC DROP //", output)
            self.assertIn("DICTIONARY RELIC // CACHE LOCKED", output)
            self.assertIn("3 headwords", output)
            self.assertIn("replay // no llm calls", output)
            self.assertIn("quality_score distribution", output)
            self.assertIn("occurrence count distribution", output)
            self.assertEqual(stderr.getvalue(), "")

    def test_show_distribution_reports_missing_file_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            missing = Path(workspace) / "absent.jsonl"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = show_distribution(jsonl_path=missing, model="gemma4:31b")

            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("cannot read distribution source", stderr.getvalue())
            self.assertIn("--clean-dictionary-candidates", stderr.getvalue())

    def test_main_with_show_distribution_skips_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "relics.jsonl"
            jsonl_path.write_text(
                json.dumps({"quality_score": 0.5, "count": 7}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.run_pipeline") as mock_pipeline:
                exit_code = main(["--show-distribution", str(jsonl_path)])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertIn("RELIC DROP //", stdout.getvalue())

    def test_link_cleaned_examples_builds_stage_twelve_without_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)
            (output_dir / "10_cleaned_example_sentences.csv").write_text(
                "\n".join(
                    [
                        "document,page,sentence_index,source_sentence,cleaned_sentence,cleanup_action,cleanup_reason,model_confidence,source_similarity,source_quality_score,source_matched_canonical_key_count,source_covered_canonical_key_count,source_covered_canonical_keys,source_matched_canonical_keys,cleaned_quality_score,cleaned_matched_canonical_key_count,cleaned_covered_canonical_key_count,cleaned_covered_canonical_keys,cleaned_matched_canonical_keys",
                        "sample.txt,1,1,점심을 먹었다.,점심을 먹었다.,keep,,0.0,1.0,0.5,3,3,점심 | 먹다 | 밥,점심 | 먹다 | 밥,0.9,3,3,점심 | 먹다 | 밥,점심 | 먹다 | 밥",
                    ]
                ) + "\n",
                encoding="utf-8-sig",
            )
            (output_dir / "11_cleaned_dictionary_candidates.jsonl").write_text(
                json.dumps(
                    {
                        "canonical_key": "먹다",
                        "display": "먹다",
                        "example": "원래 예문",
                        "quality_score": 0.8,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = link_cleaned_examples(output_dir=output_dir)

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue((output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").exists())
            raw_line = (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[0]
            row = json.loads(raw_line)
            self.assertTrue(row["example_replaced_with_cleaned_sentence"])
            self.assertEqual(row["example"], "점심을 먹었다.")
            self.assertEqual(row["curation_status"], "rewrite_ready")
            self.assertTrue(raw_line.startswith('{"canonical_key":'))
            self.assertTrue(raw_line.endswith('"example_replaced_with_cleaned_sentence": true}'))
            self.assertNotIn("original_example", row)
            self.assertNotIn("cleaned_example_document", row)

    def test_main_with_link_cleaned_examples_skips_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)
            (output_dir / "10_cleaned_example_sentences.csv").write_text(
                "\n".join(
                    [
                        "document,page,sentence_index,source_sentence,cleaned_sentence,cleanup_action,cleanup_reason,model_confidence,source_similarity,source_quality_score,source_matched_canonical_key_count,source_covered_canonical_key_count,source_covered_canonical_keys,source_matched_canonical_keys,cleaned_quality_score,cleaned_matched_canonical_key_count,cleaned_covered_canonical_key_count,cleaned_covered_canonical_keys,cleaned_matched_canonical_keys",
                        "sample.txt,1,1,점심을 먹었다.,점심을 먹었다.,keep,,0.0,1.0,0.5,3,3,점심 | 먹다 | 밥,점심 | 먹다 | 밥,0.9,3,3,점심 | 먹다 | 밥,점심 | 먹다 | 밥",
                    ]
                ) + "\n",
                encoding="utf-8-sig",
            )
            (output_dir / "11_cleaned_dictionary_candidates.jsonl").write_text(
                json.dumps({"canonical_key": "먹다", "display": "먹다", "example": "원래 예문"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.run_pipeline") as mock_pipeline:
                exit_code = main(["--link-cleaned-examples", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertIn("12_dictionary_candidates_with_cleaned_examples.jsonl", stdout.getvalue())

    def test_main_with_generate_missing_examples_uses_pipeline_cleanup_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)

            with patch("wordbyword.cli.run_pipeline") as mock_pipeline:
                mock_pipeline.return_value = PipelineResult(
                    documents=0,
                    extracted_pages=0,
                    sentences=0,
                    token_occurrences=0,
                    vocabulary_terms=0,
                    surface_words=0,
                    output_dir=output_dir,
                )
                exit_code = main(["--generate-missing-examples", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(mock_pipeline.called)
            self.assertTrue(mock_pipeline.call_args.kwargs["generate_missing_examples"])

    def test_main_with_retry_generated_examples_uses_pipeline_cleanup_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)

            with patch("wordbyword.cli.run_pipeline") as mock_pipeline:
                mock_pipeline.return_value = PipelineResult(
                    documents=0,
                    extracted_pages=0,
                    sentences=0,
                    token_occurrences=0,
                    vocabulary_terms=0,
                    surface_words=0,
                    output_dir=output_dir,
                )
                exit_code = main(["--retry-generated-examples", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(mock_pipeline.called)
            self.assertTrue(mock_pipeline.call_args.kwargs["retry_generated_examples"])
            self.assertFalse(mock_pipeline.call_args.kwargs["generate_missing_examples"])

    def test_main_with_retry_generated_examples_rejects_generate_missing_examples_combo(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as error:
                main(["--retry-generated-examples", "--generate-missing-examples"])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("--retry-generated-examples cannot be combined", stderr.getvalue())

    def test_prompt_generated_example_retry_defaults_to_yes(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stdin.isatty", return_value=True), patch(
            "builtins.input",
            return_value="",
        ):
            should_retry = prompt_generated_example_retry(
                summary=attempt_summary(attempt_number=1, failed_count=2),
                output_dir=Path("/tmp/output"),
                model="gemma4:31b",
            )

        self.assertTrue(should_retry)
        self.assertIn("13_generated_example_attempts.jsonl", stdout.getvalue())
        self.assertIn("13_generated_example_failures.jsonl", stdout.getvalue())

    def test_prompt_generated_example_retry_accepts_no(self) -> None:
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"):
            should_retry = prompt_generated_example_retry(
                summary=attempt_summary(attempt_number=2, failed_count=1),
                output_dir=Path("/tmp/output"),
                model="gemma4:31b",
            )

        self.assertFalse(should_retry)

    def test_prompt_generated_example_retry_skips_noninteractive_runs(self) -> None:
        with patch("sys.stdin.isatty", return_value=False):
            should_retry = prompt_generated_example_retry(
                summary=attempt_summary(attempt_number=1, failed_count=3),
                output_dir=Path("/tmp/output"),
                model="gemma4:31b",
            )

        self.assertFalse(should_retry)


def attempt_summary(*, attempt_number: int, failed_count: int) -> GeneratedExampleAttemptSummary:
    return GeneratedExampleAttemptSummary(
        attempt_number=attempt_number,
        attempted_count=failed_count,
        generated_count=0,
        failed_count=failed_count,
        succeeded_row_ids=[],
        failed_row_ids=[f"r{index:04d}" for index in range(1, failed_count + 1)],
        attempt_rows=[],
        failure_rows=[],
    )


if __name__ == "__main__":
    unittest.main()
