from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.dictionary_build.artifacts import (
    ArtifactId,
    artifact_event_name,
    artifact_filename,
    artifact_path,
    existing_artifact_ids,
    legacy_output_filenames,
    optional_artifacts_from,
    output_filenames,
    pipeline_stage_rows,
)
from wordbyword.dictionary_build.output_metadata import refresh_saved_output_metadata


class DictionaryArtifactCatalogTest(unittest.TestCase):
    def test_current_filename_contract_is_preserved(self) -> None:
        self.assertEqual(
            output_filenames(),
            [
                "00_summary.json",
                "01_extracted_pages.csv",
                "02_sentences.csv",
                "03_tokens.csv",
                "04_surface_words.csv",
                "05_vocabulary_raw.csv",
                "06_candidate_terms.csv",
                "07_rejected_terms.csv",
                "08_dictionary_candidates.jsonl",
                "09_example_sentences.csv",
            ],
        )
        self.assertEqual(
            output_filenames(
                include_cleaned_example_sentences=True,
                include_cleaned_dictionary_candidates=True,
                include_linked_dictionary_candidates=True,
                include_generated_dictionary_candidates=True,
                include_meaning_dictionary_candidates=True,
                include_generated_meaning_dictionary_candidates=True,
            )[-8:],
            [
                "10_cleaned_example_sentences.csv",
                "11_cleaned_dictionary_candidates.jsonl",
                "12_dictionary_candidates_with_cleaned_examples.jsonl",
                "13_vocabulary_with_generated_examples.jsonl",
                "13_generated_example_attempts.jsonl",
                "13_generated_example_failures.jsonl",
                "14_vocabulary_with_meanings.jsonl",
                "15_final_dictionary.jsonl",
            ],
        )
        self.assertIn("filtered_example_sentence.csv", legacy_output_filenames())
        self.assertIn("README.md", legacy_output_filenames())

    def test_output_filenames_can_exclude_missing_stage_thirteen_sidecars(self) -> None:
        filenames = output_filenames(
            include_generated_dictionary_candidates=True,
            include_generated_example_attempts=False,
            include_generated_example_failures=False,
        )

        self.assertIn("13_vocabulary_with_generated_examples.jsonl", filenames)
        self.assertNotIn("13_generated_example_attempts.jsonl", filenames)
        self.assertNotIn("13_generated_example_failures.jsonl", filenames)

    def test_pipeline_stage_rows_preserve_stage_order_and_exclude_sidecars(self) -> None:
        rows = pipeline_stage_rows(
            include_cleaned_example_sentences=True,
            include_cleaned_dictionary_candidates=True,
            include_linked_dictionary_candidates=True,
            include_generated_dictionary_candidates=True,
            include_meaning_dictionary_candidates=True,
            include_generated_meaning_dictionary_candidates=True,
        )

        self.assertEqual([row["stage"] for row in rows], [f"{index:02d}" for index in range(16)])
        self.assertEqual(rows[0]["file"], "00_summary.json")
        self.assertEqual(rows[-1]["file"], "15_final_dictionary.jsonl")
        self.assertNotIn("13_generated_example_attempts.jsonl", {row["file"] for row in rows})
        self.assertNotIn("13_generated_example_failures.jsonl", {row["file"] for row in rows})

    def test_optional_artifact_invalidation_order_is_preserved(self) -> None:
        self.assertEqual(
            [artifact_filename(artifact_id) for artifact_id in optional_artifacts_from(ArtifactId.CLEANED_EXAMPLE_SENTENCES)],
            [
                "10_cleaned_example_sentences.csv",
                "11_cleaned_dictionary_candidates.jsonl",
                "12_dictionary_candidates_with_cleaned_examples.jsonl",
                "13_vocabulary_with_generated_examples.jsonl",
                "13_generated_example_attempts.jsonl",
                "13_generated_example_failures.jsonl",
                "14_vocabulary_with_meanings.jsonl",
                "15_final_dictionary.jsonl",
            ],
        )
        self.assertEqual(
            [artifact_filename(artifact_id) for artifact_id in optional_artifacts_from(ArtifactId.GENERATED_EXAMPLE_DICTIONARY)],
            [
                "13_vocabulary_with_generated_examples.jsonl",
                "13_generated_example_attempts.jsonl",
                "13_generated_example_failures.jsonl",
                "14_vocabulary_with_meanings.jsonl",
                "15_final_dictionary.jsonl",
            ],
        )
        self.assertEqual(
            [artifact_filename(artifact_id) for artifact_id in optional_artifacts_from(ArtifactId.MEANING_DICTIONARY_CANDIDATES)],
            [
                "14_vocabulary_with_meanings.jsonl",
                "15_final_dictionary.jsonl",
            ],
        )
        with self.assertRaises(ValueError):
            optional_artifacts_from(ArtifactId.SUMMARY)

    def test_artifact_event_names_preserve_callback_labels(self) -> None:
        self.assertEqual(artifact_event_name(ArtifactId.EXAMPLE_SENTENCES), "09_example_sentences")
        self.assertEqual(
            artifact_event_name(ArtifactId.CLEANED_DICTIONARY_CANDIDATES),
            "11_cleaned_dictionary_candidates",
        )
        self.assertEqual(
            artifact_event_name(ArtifactId.LINKED_DICTIONARY_CANDIDATES),
            "12_dictionary_candidates_with_cleaned_examples",
        )
        self.assertEqual(
            artifact_event_name(ArtifactId.GENERATED_EXAMPLE_DICTIONARY),
            "13_vocabulary_with_generated_examples",
        )
        self.assertEqual(artifact_event_name(ArtifactId.FINAL_DICTIONARY), "15_final_dictionary")

    def test_existing_artifact_ids_follow_catalog_order(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)
            artifact_path(output_dir, ArtifactId.EXAMPLE_SENTENCES).write_text("", encoding="utf-8")
            artifact_path(output_dir, ArtifactId.SUMMARY).write_text("{}", encoding="utf-8")
            artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY).write_text("", encoding="utf-8")

            self.assertEqual(
                existing_artifact_ids(output_dir),
                [
                    ArtifactId.SUMMARY,
                    ArtifactId.EXAMPLE_SENTENCES,
                    ArtifactId.FINAL_DICTIONARY,
                ],
            )

    def test_metadata_refresh_does_not_write_generated_readme(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)
            artifact_path(output_dir, ArtifactId.SUMMARY).write_text("{}", encoding="utf-8")
            artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY).write_text("{}\n", encoding="utf-8")

            refresh_saved_output_metadata(output_dir)

            summary = json.loads(artifact_path(output_dir, ArtifactId.SUMMARY).read_text(encoding="utf-8"))
            self.assertIn("15_final_dictionary.jsonl", summary["outputs"])
            self.assertNotIn("README.md", summary["outputs"])
            self.assertFalse((output_dir / "README.md").exists())
