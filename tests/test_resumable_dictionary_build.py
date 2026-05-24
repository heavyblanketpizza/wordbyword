from __future__ import annotations

import json
import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.example_cleanup import cleaned_example_sentence_fields
from wordbyword.dictionary_build.output_files import (
    candidate_term_fields,
    example_sentence_fields,
    page_fields,
    rejected_candidate_fields,
    sentence_fields,
    token_fields,
    write_jsonl,
    write_rows,
)
from wordbyword.dictionary_build.resumable import (
    CheckpointAction,
    UnresolvedGeneratedExampleFailures,
    build_resumable_dictionary,
)
from wordbyword.dictionary_build.source_archive import (
    DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME,
    DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION,
    DictionarySourceArchiveError,
)


class ResumableDictionaryBuildTest(unittest.TestCase):
    def test_unmanaged_source_fails_before_output_artifacts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            (input_dir / "sample.txt").write_text("학교에 갑니다.", encoding="utf-8")

            with self.assertRaises(DictionarySourceArchiveError):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=root / "reference.jsonl",
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                )

            self.assertFalse(output_dir.exists())

    def test_legacy_valid_artifacts_skip_all_checkpoints_and_write_cache(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            write_all_checkpoint_artifacts(output_dir)
            statuses = []

            with patch("wordbyword.dictionary_build.resumable.run_checkpoint") as run_checkpoint:
                result = build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                    checkpoint_status=statuses.append,
                )

            run_checkpoint.assert_not_called()
            self.assertTrue(all(status.action == CheckpointAction.SKIP for status in statuses))
            self.assertTrue(all(status.reason == "legacy adopted" for status in statuses))
            self.assertEqual(result.pipeline_result.output_dir, output_dir)

            summary = json.loads(artifact_path(output_dir, ArtifactId.SUMMARY).read_text(encoding="utf-8"))
            self.assertIn("build_cache", summary)
            self.assertEqual(set(summary["build_cache"]["checkpoints"].keys()), set(checkpoint_keys()))

    def test_invalid_candidate_header_rebuilds_from_candidate_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            write_checkpoint_artifacts(output_dir, "01")
            write_checkpoint_artifacts(output_dir, "02-05")
            write_rows(
                artifact_path(output_dir, ArtifactId.CANDIDATE_TERMS),
                [{"canonical_key": "학교"}],
                fieldnames=["canonical_key"],
            )
            run_keys = []

            def fake_run(checkpoint, **kwargs):
                run_keys.append(checkpoint.key)
                write_checkpoint_artifacts(kwargs["output_dir"], checkpoint.key)

            with patch("wordbyword.dictionary_build.resumable.run_checkpoint", side_effect=fake_run):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                )

            self.assertEqual(run_keys, ["06-08", "09", "10", "11", "12", "13", "14", "15"])

    def test_force_rebuilds_every_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            write_all_checkpoint_artifacts(output_dir)
            run_keys = []

            def fake_run(checkpoint, **kwargs):
                run_keys.append(checkpoint.key)
                write_checkpoint_artifacts(kwargs["output_dir"], checkpoint.key)

            with patch("wordbyword.dictionary_build.resumable.run_checkpoint", side_effect=fake_run):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                    force=True,
                )

            self.assertEqual(run_keys, list(checkpoint_keys()))

    def test_target_stage_nine_runs_only_deterministic_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            run_keys = []

            def fake_run(checkpoint, **kwargs):
                run_keys.append(checkpoint.key)
                write_checkpoint_artifacts(kwargs["output_dir"], checkpoint.key)

            with patch("wordbyword.dictionary_build.resumable.run_checkpoint", side_effect=fake_run):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                    target_checkpoint="09",
                )

            self.assertEqual(run_keys, ["01", "02-05", "06-08", "09"])

    def test_target_stage_nine_removes_downstream_artifacts_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            write_all_checkpoint_artifacts(output_dir)

            build_resumable_dictionary(
                input_dir=input_dir,
                output_dir=output_dir,
                reference_dictionary_path=reference_path,
                include_punctuation=False,
                include_numbers=False,
                model="gemma4:31b",
            )

            build_resumable_dictionary(
                input_dir=input_dir,
                output_dir=output_dir,
                reference_dictionary_path=reference_path,
                include_punctuation=False,
                include_numbers=False,
                model="gemma4:31b",
                target_checkpoint="09",
            )

            for key in ("10", "11", "12", "13", "14", "15"):
                for artifact_id in checkpoint_artifact_ids(key):
                    self.assertFalse(artifact_path(output_dir, artifact_id).exists())

            summary = json.loads(artifact_path(output_dir, ArtifactId.SUMMARY).read_text(encoding="utf-8"))
            self.assertEqual(set(summary["build_cache"]["checkpoints"].keys()), {"01", "02-05", "06-08", "09"})
            self.assertNotIn("10_cleaned_example_sentences.csv", summary["outputs"])
            self.assertNotIn("10", {stage["stage"] for stage in summary["pipeline_stages"]})

    def test_invalid_target_checkpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))

            with self.assertRaises(ValueError):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                    target_checkpoint="99",
                )

    def test_changed_model_invalidates_llm_checkpoints_only(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            write_all_checkpoint_artifacts(output_dir)
            build_resumable_dictionary(
                input_dir=input_dir,
                output_dir=output_dir,
                reference_dictionary_path=reference_path,
                include_punctuation=False,
                include_numbers=False,
                model="gemma4:31b",
            )
            run_keys = []

            def fake_run(checkpoint, **kwargs):
                run_keys.append(checkpoint.key)
                write_checkpoint_artifacts(kwargs["output_dir"], checkpoint.key)

            with patch("wordbyword.dictionary_build.resumable.run_checkpoint", side_effect=fake_run):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="other-model",
                )

            self.assertEqual(run_keys, ["10", "11", "12", "13", "14", "15"])

    def test_changed_reference_dictionary_invalidates_meaning_checkpoints_only(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            write_all_checkpoint_artifacts(output_dir)
            build_resumable_dictionary(
                input_dir=input_dir,
                output_dir=output_dir,
                reference_dictionary_path=reference_path,
                include_punctuation=False,
                include_numbers=False,
                model="gemma4:31b",
            )
            reference_path.write_text(
                json.dumps({"canonical_key": "학교", "meaning": "다른 뜻"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            run_keys = []

            def fake_run(checkpoint, **kwargs):
                run_keys.append(checkpoint.key)
                write_checkpoint_artifacts(kwargs["output_dir"], checkpoint.key)

            with patch("wordbyword.dictionary_build.resumable.run_checkpoint", side_effect=fake_run):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                )

            self.assertEqual(run_keys, ["14", "15"])

    def test_nonempty_generated_example_failures_stop_before_meanings(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            input_dir, output_dir, reference_path = prepare_workspace(Path(workspace))
            for key in ("01", "02-05", "06-08", "09", "10", "11", "12", "13"):
                write_checkpoint_artifacts(output_dir, key)
            write_jsonl(
                artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_FAILURES),
                [{"row_id": "r0001"}],
            )

            with self.assertRaises(UnresolvedGeneratedExampleFailures):
                build_resumable_dictionary(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                    include_punctuation=False,
                    include_numbers=False,
                    model="gemma4:31b",
                )


def prepare_workspace(root: Path) -> tuple[Path, Path, Path]:
    input_dir = root / "original"
    output_dir = root / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    source_name = "textbook_government_grade1_korean_2022_term1_sample.txt"
    source_path = input_dir / source_name
    source_path.write_text("학교에 갑니다.", encoding="utf-8")
    write_dictionary_source_ledger(input_dir, source_path)
    reference_path = root / "reference.jsonl"
    reference_path.write_text(
        json.dumps({"canonical_key": "학교", "meaning": "배우는 곳."}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return input_dir, output_dir, reference_path


def write_dictionary_source_ledger(input_dir: Path, source_path: Path) -> None:
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    (input_dir / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION,
                "entries": {
                    f"sha256:{digest}": {
                        "material_id": f"sha256:{digest}",
                        "sha256": digest,
                        "byte_size": source_path.stat().st_size,
                        "extension": source_path.suffix.lower(),
                        "original_path": source_path.name,
                        "current_path": source_path.name,
                        "first_seen_at": "2026-05-24T00:00:00+09:00",
                        "last_seen_at": "2026-05-24T00:00:00+09:00",
                        "classification": {
                            "source_type": "textbook",
                            "authority": "government",
                            "grade_level": "grade1",
                            "subject_slug": "korean",
                            "curriculum_year": "2022",
                            "term_slug": "term1",
                            "title_slug": "sample",
                            "korean_title": "샘플",
                            "confidence": 0.95,
                        },
                        "rename_history": [],
                        "status": "active",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def checkpoint_keys() -> tuple[str, ...]:
    return ("01", "02-05", "06-08", "09", "10", "11", "12", "13", "14", "15")


def checkpoint_artifact_ids(key: str) -> tuple[ArtifactId, ...]:
    if key == "10":
        return (ArtifactId.CLEANED_EXAMPLE_SENTENCES,)
    if key == "11":
        return (ArtifactId.CLEANED_DICTIONARY_CANDIDATES,)
    if key == "12":
        return (ArtifactId.LINKED_DICTIONARY_CANDIDATES,)
    if key == "13":
        return (
            ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
            ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
            ArtifactId.GENERATED_EXAMPLE_FAILURES,
        )
    if key == "14":
        return (ArtifactId.MEANING_DICTIONARY_CANDIDATES,)
    if key == "15":
        return (ArtifactId.FINAL_DICTIONARY,)
    raise AssertionError(f"unknown checkpoint: {key}")


def write_all_checkpoint_artifacts(output_dir: Path) -> None:
    for key in ("01", "02-05", "06-08", "09", "10", "11", "12", "13", "14", "15"):
        write_checkpoint_artifacts(output_dir, key)


def write_checkpoint_artifacts(output_dir: Path, key: str) -> None:
    if key == "01":
        write_rows(
            artifact_path(output_dir, ArtifactId.EXTRACTED_PAGES),
            [
                {
                    "document": "sample.txt",
                    "page": "",
                    "raw_character_count": 8,
                    "clean_character_count": 8,
                    "text": "학교에 갑니다.",
                }
            ],
            fieldnames=page_fields(),
        )
        return
    if key == "02-05":
        write_rows(
            artifact_path(output_dir, ArtifactId.SENTENCES),
            [{"document": "sample.txt", "page": "", "sentence_index": 1, "sentence": "학교에 갑니다."}],
            fieldnames=sentence_fields(),
        )
        write_rows(
            artifact_path(output_dir, ArtifactId.TOKENS),
            [
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "token_index": 1,
                    "form": "학교",
                    "pos": "NNG",
                    "start": 0,
                    "end": 2,
                    "sentence": "학교에 갑니다.",
                }
            ],
            fieldnames=token_fields(),
        )
        write_rows(
            artifact_path(output_dir, ArtifactId.SURFACE_WORDS),
            [{"surface": "학교에", "count": 1, "document_count": 1, "example": "학교에 갑니다."}],
            fieldnames=["surface", "count", "document_count", "example"],
        )
        write_rows(
            artifact_path(output_dir, ArtifactId.VOCABULARY_RAW),
            [{"term": "학교", "pos": "NNG", "count": 1, "document_count": 1, "example": "학교에 갑니다."}],
            fieldnames=["term", "pos", "count", "document_count", "example"],
        )
        return
    if key == "06-08":
        row = candidate_row()
        write_rows(
            artifact_path(output_dir, ArtifactId.CANDIDATE_TERMS),
            [{field: row[field] for field in candidate_term_fields()}],
            fieldnames=candidate_term_fields(),
        )
        write_rows(artifact_path(output_dir, ArtifactId.REJECTED_TERMS), [], fieldnames=rejected_candidate_fields())
        write_jsonl(
            artifact_path(output_dir, ArtifactId.DICTIONARY_CANDIDATES),
            [{**row, "kind": "term", "variants": ["학교"], "curation_status": "candidate"}],
        )
        return
    if key == "09":
        write_rows(
            artifact_path(output_dir, ArtifactId.EXAMPLE_SENTENCES),
            [
                {
                    "document": "sample.txt",
                    "page": "",
                    "sentence_index": 1,
                    "sentence": "학교에 갑니다.",
                    "quality_score": 1.0,
                    "matched_canonical_key_count": 1,
                    "covered_canonical_key_count": 1,
                    "covered_canonical_keys": "학교",
                    "matched_canonical_keys": "학교",
                }
            ],
            fieldnames=example_sentence_fields(),
        )
        return
    if key == "10":
        write_rows(
            artifact_path(output_dir, ArtifactId.CLEANED_EXAMPLE_SENTENCES),
            [cleaned_example_row()],
            fieldnames=cleaned_example_sentence_fields(),
        )
        return
    if key == "11":
        write_jsonl(
            artifact_path(output_dir, ArtifactId.CLEANED_DICTIONARY_CANDIDATES),
            [{**candidate_row(), "variants": ["학교"], "curation_status": "confirmed"}],
        )
        return
    if key == "12":
        write_jsonl(
            artifact_path(output_dir, ArtifactId.LINKED_DICTIONARY_CANDIDATES),
            [
                {
                    **candidate_row(),
                    "variants": ["학교"],
                    "curation_status": "rewrite_ready",
                    "example_replaced_with_cleaned_sentence": True,
                }
            ],
        )
        return
    if key == "13":
        write_jsonl(
            artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_DICTIONARY),
            [{**candidate_row(), "variants": ["학교"], "example_generated_with_ollama": False}],
        )
        artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_FAILURES).write_text("", encoding="utf-8")
        artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_ATTEMPTS).write_text("", encoding="utf-8")
        return
    if key == "14":
        write_jsonl(
            artifact_path(output_dir, ArtifactId.MEANING_DICTIONARY_CANDIDATES),
            [{**candidate_row(), "variants": ["학교"], "meaning": "배우는 곳."}],
        )
        return
    if key == "15":
        write_jsonl(
            artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY),
            [{**candidate_row(), "variants": ["학교"], "meaning": "배우는 곳.", "meaning_generated_with_ollama": False}],
        )
        return
    raise AssertionError(f"unknown checkpoint: {key}")


def candidate_row() -> dict[str, object]:
    return {
        "canonical_key": "학교",
        "display": "학교",
        "kind": "term",
        "pos_group": "noun",
        "count": 1,
        "surface_count": 1,
        "document_count": 1,
        "variant_count": 1,
        "variants": "학교",
        "learning_signal_score": 0.5,
        "frequency_signal_score": 0.5,
        "dispersion_signal_score": 0.5,
        "pos_signal_score": 0.4,
        "variant_signal_score": 0.5,
        "frequency_rate_per_10k": 1.0,
        "example": "학교에 갑니다.",
        "curation_status": "candidate",
    }


def cleaned_example_row() -> dict[str, object]:
    return {
        "document": "sample.txt",
        "page": "",
        "sentence_index": 1,
        "source_sentence": "학교에 갑니다.",
        "cleaned_sentence": "학교에 갑니다.",
        "cleanup_action": "keep",
        "cleanup_reason": "",
        "model_confidence": 1.0,
        "source_similarity": 1.0,
        "source_quality_score": 1.0,
        "source_matched_canonical_key_count": 1,
        "source_covered_canonical_key_count": 1,
        "source_covered_canonical_keys": "학교",
        "source_matched_canonical_keys": "학교",
        "cleaned_quality_score": 1.0,
        "cleaned_matched_canonical_key_count": 1,
        "cleaned_covered_canonical_key_count": 1,
        "cleaned_covered_canonical_keys": "학교",
        "cleaned_matched_canonical_keys": "학교",
    }
