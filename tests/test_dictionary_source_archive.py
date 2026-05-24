from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.dictionary_build.llm import ChatMessage, CompletionResult
from wordbyword.dictionary_build.source_archive import (
    DEFAULT_DICTIONARY_SOURCE_DUPLICATE_QUARANTINE_DIRNAME,
    DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME,
    DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION,
    DictionarySourceArchiveError,
    DictionarySourceMetadata,
    dictionary_source_filename,
    discover_dictionary_source_paths,
    rename_dictionary_source_archive,
    resolve_dictionary_source_duplicates,
    scan_dictionary_source_duplicates,
    validate_managed_dictionary_source_archive,
)


class DictionarySourceArchiveTest(unittest.TestCase):
    def test_discovers_recursive_sources_and_ignores_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            (root / "grade2").mkdir(parents=True)
            (root / DEFAULT_DICTIONARY_SOURCE_DUPLICATE_QUARANTINE_DIRNAME / "batch").mkdir(parents=True)
            (root / "grade2" / "lesson.txt").write_text("학교에 갑니다.", encoding="utf-8")
            (root / DEFAULT_DICTIONARY_SOURCE_DUPLICATE_QUARANTINE_DIRNAME / "batch" / "old.txt").write_text(
                "ignore", encoding="utf-8"
            )

            paths = discover_dictionary_source_paths(root)

            self.assertEqual([path.relative_to(root).as_posix() for path in paths], ["grade2/lesson.txt"])

    def test_dry_run_plans_provenance_first_rename_without_writing_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            root.mkdir()
            source = root / "loose.txt"
            source.write_text("초등학교 2학년 국어 교과서입니다.", encoding="utf-8")
            client = RecordingClient(metadata_response())

            result = rename_dictionary_source_archive(root, client=client, model="fake", apply=False)

            self.assertTrue(result.dry_run)
            self.assertEqual(result.planned_count, 1)
            self.assertTrue(source.exists())
            self.assertFalse((root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).exists())
            self.assertEqual(
                result.plans[0].target_name,
                "textbook_government_grade2_korean_2022_term1_reading-basics.txt",
            )
            self.assertEqual(len(client.calls), 1)

    def test_apply_renames_file_and_writes_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            root.mkdir()
            source = root / "loose.txt"
            source.write_text("초등학교 2학년 국어 교과서입니다.", encoding="utf-8")

            result = rename_dictionary_source_archive(
                root,
                client=RecordingClient(metadata_response()),
                model="fake",
                apply=True,
            )

            target = root / "textbook_government_grade2_korean_2022_term1_reading-basics.txt"
            self.assertFalse(source.exists())
            self.assertTrue(target.exists())
            self.assertEqual(result.renamed_count, 1)
            ledger = json.loads((root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(ledger["schema_version"], DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION)
            entry = next(iter(ledger["entries"].values()))
            self.assertEqual(entry["original_path"], "loose.txt")
            self.assertEqual(entry["current_path"], target.name)
            self.assertEqual(entry["classification"]["source_type"], "textbook")

    def test_apply_blocks_low_confidence_without_renaming_or_writing_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            root.mkdir()
            source = root / "loose.txt"
            source.write_text("출처가 불분명한 자료입니다.", encoding="utf-8")
            low_confidence = {**metadata_response(), "confidence": 0.5}

            with self.assertRaises(DictionarySourceArchiveError):
                rename_dictionary_source_archive(
                    root,
                    client=RecordingClient(low_confidence),
                    model="fake",
                    apply=True,
                )

            self.assertTrue(source.exists())
            self.assertFalse((root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).exists())

    def test_detects_same_byte_and_cross_format_exact_text_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            root.mkdir()
            (root / "a.txt").write_text("같은 글입니다.", encoding="utf-8")
            (root / "copy.txt").write_text("같은 글입니다.", encoding="utf-8")
            (root / "same.md").write_text("같은   글입니다.", encoding="utf-8")

            groups = scan_dictionary_source_duplicates(root).duplicate_groups

            reasons = {evidence.reason for group in groups for evidence in group.evidences}
            self.assertIn("same_byte_sha256", reasons)
            self.assertIn("cross_format_exact_text", reasons)

    def test_resolver_quarantines_duplicates_and_keeps_managed_filename(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            root.mkdir()
            managed = root / "textbook_government_grade2_korean_2022_term1_reading-basics.txt"
            duplicate = root / "copy.txt"
            managed.write_text("같은 글입니다.", encoding="utf-8")
            duplicate.write_text("같은 글입니다.", encoding="utf-8")

            preview = resolve_dictionary_source_duplicates(root, apply=False)
            self.assertEqual(preview.duplicate_group_count, 1)
            self.assertEqual(preview.resolutions[0].keep_path, managed.resolve())

            result = resolve_dictionary_source_duplicates(root, apply=True)

            self.assertTrue(managed.exists())
            self.assertFalse(duplicate.exists())
            self.assertEqual(result.quarantined_count, 1)
            self.assertTrue(result.manifest_path.exists())

    def test_validation_rejects_unmanaged_sources_and_accepts_valid_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "original"
            root.mkdir()
            source = root / "textbook_government_grade2_korean_2022_term1_reading-basics.txt"
            source.write_text("초등학교 2학년 국어 교과서입니다.", encoding="utf-8")

            with self.assertRaises(DictionarySourceArchiveError):
                validate_managed_dictionary_source_archive(root)

            write_valid_ledger(root, source)
            result = validate_managed_dictionary_source_archive(root)

            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(result.metadata_by_sha256[digest]["source_type"], "textbook")


class RecordingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[list[ChatMessage]] = []

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        self.calls.append(messages)
        return CompletionResult(
            provider="ollama",
            model="fake",
            content=json.dumps(self.response, ensure_ascii=False),
        )


def metadata_response() -> dict[str, object]:
    return {
        "source_type": "textbook",
        "authority": "government",
        "grade_level": "grade2",
        "subject_slug": "korean",
        "curriculum_year": "2022",
        "term_slug": "term1",
        "title_slug": "reading-basics",
        "korean_title": "읽기 기초",
        "confidence": 0.95,
    }


def write_valid_ledger(root: Path, source: Path) -> None:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata = DictionarySourceMetadata.model_validate(metadata_response()).model_dump(mode="json")
    self_filename = dictionary_source_filename(DictionarySourceMetadata.model_validate(metadata_response()), ".txt")
    assert source.name == self_filename
    (root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION,
                "entries": {
                    f"sha256:{digest}": {
                        "material_id": f"sha256:{digest}",
                        "sha256": digest,
                        "byte_size": source.stat().st_size,
                        "extension": ".txt",
                        "original_path": source.name,
                        "current_path": source.name,
                        "first_seen_at": "2026-05-24T00:00:00+09:00",
                        "last_seen_at": "2026-05-24T00:00:00+09:00",
                        "classification": metadata,
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
