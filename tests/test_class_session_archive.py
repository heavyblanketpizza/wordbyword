from __future__ import annotations

import csv
import io
import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import fitz
from docx import Document

from wordbyword.class_archive.archive import (
    ARCHIVE_DUPLICATE_MANIFEST_SCHEMA_VERSION,
    DEFAULT_ARCHIVE_LEDGER_FILENAME,
    DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME,
    DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
    ArchiveRenameMetadata,
    ClassSessionArchiveError,
    archive_filename,
    build_archived_material,
    discover_archive_duplicates,
    discover_archive_near_duplicate_candidates,
    discover_archive_materials,
    discover_archive_material_paths,
    normalize_grade_level,
    rename_class_session_archive,
    resolve_archive_duplicates,
    safe_slug,
)
from wordbyword.class_archive.live_lexicon import (
    DEFAULT_CANONICAL_DICTIONARY_PATH,
    DEFAULT_LIVE_LEXICON_PATH,
    DEFAULT_SUPPLEMENTAL_LEXICON_PATH,
    DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
    DEFAULT_USAGE_CANDIDATES_PATH,
    update_live_lexicon,
)
from wordbyword.class_archive.usage_signals import (
    DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT,
    UsageSignalBuildError,
    build_usage_signals,
    discover_usage_signal_documents,
)
from wordbyword.cli import build_parser, main
from wordbyword.dictionary_build.llm import ChatMessage, CompletionResult


class ClassSessionArchiveRenameTest(unittest.TestCase):
    def test_discovers_docx_and_pdf_materials_only(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            write_docx(archive / "material.docx", "빛은 작은 알갱이처럼 움직이기도 해요.")
            write_pdf(archive / "material.pdf", "Matter can change into many shapes.")
            (archive / "notes.txt").write_text("ignore me", encoding="utf-8")

            materials = discover_archive_materials(archive)

            self.assertEqual([material.name for material in materials], ["material.docx", "material.pdf"])
            self.assertTrue(materials[0].text_excerpt)
            self.assertTrue(materials[1].text_excerpt)

    def test_dry_run_plans_docx_and_pdf_renames_without_touching_files(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            write_docx(archive / "material.docx", "빛과 원자에 대해 1학년 어린이가 읽는 글입니다.")
            write_pdf(archive / "material 2.pdf", "Light is energy that helps us see objects.")
            client = RecordingArchiveClient(
                {
                    "grade_level": "1학년",
                    "topic_slug": "physics",
                    "short_title_slug": "light-and-atoms",
                    "korean_title": "빛과 원자",
                    "confidence": 0.91,
                }
            )

            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=False,
            )

            self.assertTrue(result.dry_run)
            self.assertEqual(result.planned_count, 2)
            self.assertEqual(result.renamed_count, 0)
            self.assertTrue((archive / "material.docx").exists())
            self.assertTrue((archive / "material 2.pdf").exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_LEDGER_FILENAME).exists())
            self.assertEqual(len(client.calls), 2)
            for plan in result.plans:
                self.assertRegex(plan.target_name, r"^\d{4}-\d{2}-\d{2}_grade1-physics-light-and-atoms\.")
                self.assertEqual(plan.metadata.grade_level, "grade1")

    def test_apply_renames_files_and_writes_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            write_docx(archive / "material.docx", "초등학교 2학년이 읽는 화학 이야기입니다.")
            client = RecordingArchiveClient(
                {
                    "grade_level": "grade2",
                    "topic_slug": "chemistry",
                    "short_title_slug": "plastic-recycling",
                    "korean_title": "플라스틱 재활용",
                    "confidence": 0.88,
                }
            )

            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=True,
            )

            self.assertFalse((archive / "material.docx").exists())
            renamed_files = sorted(path.name for path in archive.glob("*.docx"))
            self.assertEqual(len(renamed_files), 1)
            self.assertRegex(renamed_files[0], r"^\d{4}-\d{2}-\d{2}_grade2-chemistry-plastic-recycling\.docx$")
            self.assertEqual(result.renamed_count, 1)

            ledger = json.loads((archive / DEFAULT_ARCHIVE_LEDGER_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(ledger["schema_version"], "wordbyword.class_session_archive.v1")
            entries = ledger["entries"]
            self.assertEqual(len(entries), 1)
            entry = next(iter(entries.values()))
            self.assertEqual(entry["original_name"], "material.docx")
            self.assertEqual(entry["current_name"], renamed_files[0])
            self.assertEqual(entry["classification"]["grade_level"], "grade2")
            self.assertEqual(entry["rename_history"][0]["from"], "material.docx")

    def test_existing_archive_name_is_silent_without_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            existing = archive / "2026-05-10_grade1-physics-light.docx"
            write_docx(existing, "이미 이름이 정리된 자료입니다.")
            client = RecordingArchiveClient({})

            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=False,
            )

            self.assertEqual(result.plans, ())
            self.assertEqual(result.skipped_count, 0)
            self.assertEqual(len(client.calls), 0)
            self.assertTrue(existing.exists())

    def test_refuses_unmanaged_duplicate_of_existing_managed_docx_before_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            existing = archive / "2026-05-10_grade1-reading-apples.docx"
            duplicate = archive / "copy of apples.docx"
            write_docx(existing, "사과를 먹고 학교에 갑니다.")
            shutil.copyfile(existing, duplicate)
            client = RecordingArchiveClient({})

            with self.assertRaises(ClassSessionArchiveError) as context:
                rename_class_session_archive(
                    archive,
                    client=client,
                    model="fake-ollama",
                    apply=True,
                )

            message = str(context.exception)
            self.assertIn("duplicate class-session archive files detected", message)
            self.assertIn("same_format_sha256", message)
            self.assertIn(existing.name, message)
            self.assertIn(duplicate.name, message)
            self.assertEqual(len(client.calls), 0)
            self.assertTrue(existing.exists())
            self.assertTrue(duplicate.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_LEDGER_FILENAME).exists())

    def test_refuses_two_unmanaged_duplicate_docx_files_before_partial_rename(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, "중복된 수업 자료입니다.")
            shutil.copyfile(first, second)
            client = RecordingArchiveClient(
                {
                    "grade_level": "grade1",
                    "topic_slug": "reading",
                    "short_title_slug": "duplicate-material",
                    "korean_title": "중복 자료",
                    "confidence": 0.9,
                }
            )

            with self.assertRaises(ClassSessionArchiveError) as context:
                rename_class_session_archive(
                    archive,
                    client=client,
                    model="fake-ollama",
                    apply=True,
                )

            message = str(context.exception)
            self.assertIn("same_format_sha256", message)
            self.assertIn(first.name, message)
            self.assertIn(second.name, message)
            self.assertEqual(len(client.calls), 0)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_LEDGER_FILENAME).exists())

    def test_refuses_cross_format_exact_text_duplicate_before_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            docx = archive / "material.docx"
            pdf = archive / "material.pdf"
            text = duplicate_text()
            write_docx(docx, text)
            write_pdf(pdf, text)
            client = RecordingArchiveClient({})

            with self.assertRaises(ClassSessionArchiveError) as context:
                rename_class_session_archive(
                    archive,
                    client=client,
                    model="fake-ollama",
                    apply=False,
                )

            message = str(context.exception)
            self.assertIn("cross_format_text_fingerprint", message)
            self.assertIn(docx.name, message)
            self.assertIn(pdf.name, message)
            self.assertEqual(len(client.calls), 0)

    def test_refuses_model_confirmed_near_duplicate_before_rename_classification(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, near_duplicate_text("Students circle the classroom sentence."))
            write_docx(second, near_duplicate_text("Students circle the classroom sentences."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    first.name,
                    second.name,
                    verdict="same_material",
                    confidence=0.91,
                    qualities={first.name: 0.82, second.name: 0.61},
                ),
            )

            with self.assertRaises(ClassSessionArchiveError) as context:
                rename_class_session_archive(
                    archive,
                    client=client,
                    model="fake-ollama",
                    apply=True,
                )

            message = str(context.exception)
            self.assertIn("near_text_model_review", message)
            self.assertIn("--include-near-duplicates", message)
            self.assertIn(first.name, message)
            self.assertIn(second.name, message)
            self.assertEqual(client.near_duplicate_calls, 1)
            self.assertEqual(client.rename_calls, 0)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_LEDGER_FILENAME).exists())

    def test_uncertain_model_review_does_not_block_archive_rename(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, near_duplicate_text("Students answer with a short Korean phrase."))
            write_docx(second, near_duplicate_text("Students answer with a full Korean sentence."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    first.name,
                    second.name,
                    verdict="uncertain",
                    confidence=0.63,
                    qualities={first.name: 0.73, second.name: 0.77},
                ),
                rename_response={
                    "grade_level": "grade1",
                    "topic_slug": "reading",
                    "short_title_slug": "practice",
                    "korean_title": "읽기 자료",
                    "confidence": 0.75,
                },
            )

            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=False,
            )

            self.assertEqual(result.planned_count, 2)
            self.assertEqual(client.near_duplicate_calls, 1)
            self.assertEqual(client.rename_calls, 2)

    def test_same_suffix_different_content_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            write_docx(archive / "material.docx", "첫 번째 수업 자료입니다.")
            write_docx(archive / "material 2.docx", "두 번째 수업 자료입니다.")
            client = RecordingArchiveClient(
                {
                    "grade_level": "grade1",
                    "topic_slug": "reading",
                    "short_title_slug": "practice",
                    "korean_title": "읽기 자료",
                    "confidence": 0.75,
                }
            )

            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=False,
            )

            self.assertEqual(result.planned_count, 2)
            self.assertEqual(len(client.calls), 2)

    def test_mixed_archive_filters_named_files_and_naturally_sorts_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            named = archive / "2026-05-10_grade1-physics-light.docx"
            write_docx(named, "이미 이름이 정리된 자료입니다.")
            write_docx(archive / "material 10.docx", "열 번째 자료입니다.")
            write_docx(archive / "material 2.docx", "두 번째 자료입니다.")
            write_docx(archive / "material.docx", "첫 번째 자료입니다.")
            client = RecordingArchiveClient(
                {
                    "grade_level": "grade1",
                    "topic_slug": "reading",
                    "short_title_slug": "practice",
                    "korean_title": "읽기 자료",
                    "confidence": 0.75,
                }
            )

            paths = discover_archive_material_paths(archive)
            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=False,
            )

            self.assertEqual([path.name for path in paths], ["material.docx", "material 2.docx", "material 10.docx"])
            self.assertEqual([plan.original_name for plan in result.plans], ["material.docx", "material 2.docx", "material 10.docx"])
            self.assertEqual(archive_call_filenames(client), ["material.docx", "material 2.docx", "material 10.docx"])
            self.assertNotIn(named.name, [plan.original_name for plan in result.plans])
            self.assertTrue(named.exists())

    def test_empty_pdf_gets_cautious_unknown_name_without_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            document = fitz.open()
            document.new_page()
            document.save(archive / "material.pdf")
            document.close()
            client = RecordingArchiveClient({})

            result = rename_class_session_archive(
                archive,
                client=client,
                model="fake-ollama",
                apply=False,
            )

            self.assertEqual(len(client.calls), 0)
            self.assertRegex(result.plans[0].target_name, r"_grade-unknown-topic-unknown-material\.pdf$")

    def test_refuses_ledger_outside_archive(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            write_docx(archive / "material.docx", "테스트 자료입니다.")

            with self.assertRaises(ClassSessionArchiveError):
                rename_class_session_archive(
                    archive,
                    client=RecordingArchiveClient({}),
                    model="fake-ollama",
                    ledger_path=Path(workspace) / "outside.json",
                )


class ClassSessionArchiveHelpersTest(unittest.TestCase):
    def test_grade_and_slug_normalization(self) -> None:
        self.assertEqual(normalize_grade_level("1학년"), "grade1")
        self.assertEqual(normalize_grade_level("Grade 3"), "grade3")
        self.assertEqual(normalize_grade_level("중1"), "grade7")
        self.assertEqual(normalize_grade_level("고등학교 1학년"), "grade10")
        self.assertEqual(normalize_grade_level("중학생"), "grade-unknown")
        self.assertEqual(safe_slug("Light & Atoms!", fallback="material"), "light-atoms")
        self.assertEqual(safe_slug("빛과 원자", fallback="material"), "material")

    def test_archive_filename_compacts_duplicate_parts(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "material.docx"
            write_docx(path, "빛에 대한 글입니다.")
            material = build_archived_material(path, ledger_entries={})
            metadata = ArchiveRenameMetadata(
                grade_level="grade1",
                topic_slug="physics",
                short_title_slug="physics",
                confidence=0.5,
            )

            name = archive_filename(material, metadata)

            self.assertTrue(re.search(r"_grade1-physics\.docx$", name))

    def test_near_duplicate_candidate_generation_finds_same_format_resaved_text(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "lesson-a.docx"
            second = archive / "lesson-b.docx"
            write_docx(first, near_duplicate_text("Students underline the apple sentence."))
            write_docx(second, near_duplicate_text("Students underline the apple sentences."))

            candidates = discover_archive_near_duplicate_candidates(archive)

            self.assertEqual(len(candidates), 1)
            self.assertEqual({path.name for path in candidates[0].paths}, {first.name, second.name})
            self.assertGreaterEqual(candidates[0].dice_similarity, 0.88)

    def test_near_duplicate_candidate_generation_finds_cross_format_small_text_drift(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            docx = archive / "lesson.docx"
            pdf = archive / "lesson.pdf"
            write_docx(docx, near_duplicate_text("Students answer the final question in Korean."))
            write_pdf(pdf, near_duplicate_text("Students answer the final question in English."))

            candidates = discover_archive_near_duplicate_candidates(archive)

            self.assertEqual(len(candidates), 1)
            self.assertEqual({path.name for path in candidates[0].paths}, {docx.name, pdf.name})

    def test_near_duplicate_candidate_generation_ignores_low_similarity_text(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            write_docx(archive / "reading.docx", near_duplicate_text("Students read about apples and classrooms."))
            write_docx(archive / "science.docx", different_long_text())

            self.assertEqual(discover_archive_near_duplicate_candidates(archive), ())


class ClassUsageSignalBuildTest(unittest.TestCase):
    def test_usage_signal_build_reads_only_named_docx_and_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            output = root / "usage-signal-build"
            archive.mkdir()
            named_docx = archive / "2026-05-10_grade1-reading-apples.docx"
            named_pdf = archive / "2026-05-11_grade1-science-light.pdf"
            rough_docx = archive / "material.docx"
            ignored_txt = archive / "2026-05-12_grade1-reading-notes.txt"
            write_docx(named_docx, "사과를 먹고 학교에 갑니다. 친구와 책을 읽습니다.")
            write_pdf(named_pdf, "Light helps us see classroom objects.")
            write_docx(rough_docx, "이 자료는 이름이 아직 정리되지 않았습니다.")
            ignored_txt.write_text("이 텍스트 파일은 사용하지 않습니다.", encoding="utf-8")

            result = build_usage_signals(
                archive_root=archive,
                output_root=output,
            )

            self.assertEqual(result.documents, 2)
            self.assertEqual(result.extracted_pages, 2)
            for filename in (
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
            ):
                self.assertTrue((output / filename).is_file(), filename)

            documents = discover_usage_signal_documents(archive)
            self.assertEqual([path.name for path in documents], [named_docx.name, named_pdf.name])
            self.assertEqual(
                [path.name for path in discover_usage_signal_documents(archive, target_grade="grade1")],
                [named_docx.name, named_pdf.name],
            )
            self.assertEqual(discover_usage_signal_documents(archive, target_grade="grade7"), [])
            summary = json.loads((output / "00_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["document_count"], 2)
            self.assertEqual(summary["documents"], [named_docx.name, named_pdf.name])

            with (output / "01_extracted_pages.csv").open(encoding="utf-8-sig") as handle:
                extracted_pages = list(csv.DictReader(handle))
            self.assertEqual([row["document"] for row in extracted_pages], [named_docx.name, named_pdf.name])
            self.assertIn("사과를 먹고 학교에 갑니다", extracted_pages[0]["text"])
            self.assertNotIn("아직 정리되지", json.dumps(extracted_pages, ensure_ascii=False))

            with (output / "06_candidate_terms.csv").open(encoding="utf-8-sig") as handle:
                candidate_terms = list(csv.DictReader(handle))
            self.assertGreater(len(candidate_terms), 0)
            self.assertIn("먹다", {row["canonical_key"] for row in candidate_terms})

    def test_usage_signal_build_refuses_duplicate_managed_docx_files_before_writing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            output = root / "usage-signal-build"
            archive.mkdir()
            first = archive / "2026-05-10_grade1-reading-apples.docx"
            second = archive / "2026-05-11_grade1-reading-apples-copy.docx"
            write_docx(first, "사과를 먹고 학교에 갑니다.")
            shutil.copyfile(first, second)

            with self.assertRaises(UsageSignalBuildError) as context:
                build_usage_signals(
                    archive_root=archive,
                    output_root=output,
                )

            message = str(context.exception)
            self.assertIn("duplicate class-session archive files detected", message)
            self.assertIn("same_format_sha256", message)
            self.assertIn(first.name, message)
            self.assertIn(second.name, message)
            self.assertFalse(output.exists())

    def test_usage_signal_build_refuses_cross_format_exact_text_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            output = root / "usage-signal-build"
            archive.mkdir()
            docx = archive / "2026-05-10_grade1-reading-apples.docx"
            pdf = archive / "2026-05-10_grade1-reading-apples.pdf"
            text = duplicate_text()
            write_docx(docx, text)
            write_pdf(pdf, text)

            with self.assertRaises(UsageSignalBuildError) as context:
                build_usage_signals(
                    archive_root=archive,
                    output_root=output,
                )

            message = str(context.exception)
            self.assertIn("cross_format_text_fingerprint", message)
            self.assertIn(docx.name, message)
            self.assertIn(pdf.name, message)
            self.assertFalse(output.exists())

    def test_usage_signal_build_refuses_model_confirmed_near_duplicate_before_writing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            output = root / "usage-signal-build"
            archive.mkdir()
            first = archive / "2026-05-10_grade1-reading-apples.docx"
            second = archive / "2026-05-11_grade1-reading-apples-review.docx"
            write_docx(first, near_duplicate_text("Students circle the apple word."))
            write_docx(second, near_duplicate_text("Students circle the apple words."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    first.name,
                    second.name,
                    verdict="same_material",
                    confidence=0.9,
                    qualities={first.name: 0.82, second.name: 0.7},
                )
            )

            with self.assertRaises(UsageSignalBuildError) as context:
                build_usage_signals(
                    archive_root=archive,
                    output_root=output,
                    near_duplicate_client=client,
                    near_duplicate_model="fake-ollama",
                )

            message = str(context.exception)
            self.assertIn("near_text_model_review", message)
            self.assertIn(first.name, message)
            self.assertIn(second.name, message)
            self.assertFalse(output.exists())
            self.assertEqual(client.near_duplicate_calls, 1)


class ClassSessionArchiveDuplicateResolverTest(unittest.TestCase):
    def test_prompt_yes_quarantines_same_format_duplicates_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            kept = archive / "2026-05-10_grade1-reading-apples.docx"
            duplicate = archive / "copy of apples.docx"
            write_docx(kept, "사과를 먹고 학교에 갑니다.")
            shutil.copyfile(kept, duplicate)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
                patch("sys.stdin", InteractiveInput("y\n")),
            ):
                exit_code = main(["--resolve-archive-duplicates", str(archive)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(kept.exists())
            self.assertFalse(duplicate.exists())
            quarantine_files = sorted((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).glob("*/*.docx"))
            self.assertEqual([path.name for path in quarantine_files], [duplicate.name])
            manifest = json.loads((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], ARCHIVE_DUPLICATE_MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["resolutions"][0]["kept"]["name"], kept.name)
            self.assertEqual(manifest["resolutions"][0]["quarantined"][0]["name"], duplicate.name)
            self.assertEqual(discover_archive_duplicates(archive), ())
            self.assertEqual([path.name for path in discover_usage_signal_documents(archive)], [kept.name])
            self.assertIn("archive duplicates resolved", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_cross_format_duplicate_resolution_keeps_docx_over_older_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            pdf = archive / "2026-05-09_grade1-reading-apples.pdf"
            docx = archive / "material.docx"
            text = duplicate_text()
            write_pdf(pdf, text)
            write_docx(docx, text)

            result = resolve_archive_duplicates(
                archive,
                apply=True,
                clock=fixed_archive_clock,
            )

            self.assertTrue(result.applied)
            self.assertTrue(docx.exists())
            self.assertFalse(pdf.exists())
            self.assertEqual(result.resolutions[0].keep_path.name, docx.name)
            self.assertEqual(result.resolutions[0].quarantine_paths[0].name, pdf.name)
            self.assertTrue((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME / "20260524T120000+0000" / pdf.name).exists())

    def test_same_format_duplicate_resolution_prefers_managed_name(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            unmanaged = archive / "material.docx"
            managed = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(unmanaged, "사과를 먹고 학교에 갑니다.")
            shutil.copyfile(unmanaged, managed)

            result = resolve_archive_duplicates(
                archive,
                apply=True,
                clock=fixed_archive_clock,
            )

            self.assertTrue(managed.exists())
            self.assertFalse(unmanaged.exists())
            self.assertEqual(result.resolutions[0].keep_path.name, managed.name)

    def test_prompt_no_leaves_duplicates_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, "중복된 수업 자료입니다.")
            shutil.copyfile(first, second)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
                patch("sys.stdin", InteractiveInput("n\n")),
            ):
                exit_code = main(["--resolve-archive-duplicates", str(archive)])

            self.assertEqual(exit_code, 1)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).exists())
            self.assertIn("cancelled", stderr.getvalue())

    def test_noninteractive_resolver_without_yes_leaves_duplicates_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, "중복된 수업 자료입니다.")
            shutil.copyfile(first, second)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
                patch("sys.stdin", io.StringIO("")),
            ):
                exit_code = main(["--resolve-archive-duplicates", str(archive)])

            self.assertEqual(exit_code, 1)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).exists())
            self.assertIn("--yes", stderr.getvalue())

    def test_yes_applies_recommended_resolution_without_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, "중복된 수업 자료입니다.")
            shutil.copyfile(first, second)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
                patch("sys.stdin", io.StringIO("")),
            ):
                exit_code = main(["--resolve-archive-duplicates", str(archive), "--yes"])

            self.assertEqual(exit_code, 0)
            self.assertTrue(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).is_dir())
            self.assertNotIn("Apply this duplicate quarantine plan?", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_near_duplicate_prompt_yes_quarantines_lower_quality_file_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            kept = archive / "2026-05-10_grade1-reading-apples.docx"
            duplicate = archive / "copy of apples.docx"
            write_docx(kept, near_duplicate_text("Students answer the classroom question."))
            write_docx(duplicate, near_duplicate_text("Students answer the classroom questions."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    kept.name,
                    duplicate.name,
                    verdict="same_material",
                    confidence=0.92,
                    qualities={kept.name: 0.93, duplicate.name: 0.72},
                )
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
                patch("sys.stdin", InteractiveInput("y\n")),
                patch("wordbyword.cli.app.ollama_client", return_value=client),
                patch("wordbyword.cli.app.warn_if_model_unload_fails"),
            ):
                exit_code = main(["--resolve-archive-duplicates", str(archive), "--include-near-duplicates"])

            self.assertEqual(exit_code, 0)
            self.assertTrue(kept.exists())
            self.assertFalse(duplicate.exists())
            manifest = json.loads((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME / "manifest.json").read_text(encoding="utf-8"))
            reason = manifest["resolutions"][0]["reasons"][0]
            self.assertEqual(reason["reason"], "near_text_model_review")
            self.assertEqual(reason["metadata"]["verdict"], "same_material")
            self.assertEqual(manifest["resolutions"][0]["kept"]["name"], kept.name)
            self.assertEqual(manifest["resolutions"][0]["quarantined"][0]["name"], duplicate.name)
            self.assertIn("near_duplicate_reviews: 1", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_near_duplicate_resolution_quality_wins_before_docx_format(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            docx = archive / "material.docx"
            pdf = archive / "material.pdf"
            write_docx(docx, near_duplicate_text("Students write one short answer."))
            write_pdf(pdf, near_duplicate_text("Students write one complete answer."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    docx.name,
                    pdf.name,
                    verdict="same_material",
                    confidence=0.93,
                    qualities={docx.name: 0.70, pdf.name: 0.92},
                )
            )

            result = resolve_archive_duplicates(
                archive,
                apply=False,
                include_near_duplicates=True,
                near_duplicate_client=client,
                near_duplicate_model="fake-ollama",
            )

            self.assertEqual(result.resolutions[0].keep_path.name, pdf.name)
            self.assertEqual(result.resolutions[0].quarantine_paths[0].name, docx.name)

    def test_near_duplicate_resolution_uses_docx_tiebreak_inside_quality_deadband(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            pdf = archive / "material.pdf"
            docx = archive / "material.docx"
            write_pdf(pdf, near_duplicate_text("Students read the sentence aloud."))
            write_docx(docx, near_duplicate_text("Students read the sentences aloud."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    docx.name,
                    pdf.name,
                    verdict="same_material",
                    confidence=0.93,
                    qualities={docx.name: 0.88, pdf.name: 0.91},
                )
            )

            result = resolve_archive_duplicates(
                archive,
                apply=False,
                include_near_duplicates=True,
                near_duplicate_client=client,
                near_duplicate_model="fake-ollama",
            )

            self.assertEqual(result.resolutions[0].keep_path.name, docx.name)

    def test_near_duplicate_resolution_uses_managed_tiebreak_inside_quality_deadband(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            unmanaged = archive / "material.docx"
            managed = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(unmanaged, near_duplicate_text("Students mark the new word."))
            write_docx(managed, near_duplicate_text("Students mark the new words."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    unmanaged.name,
                    managed.name,
                    verdict="same_material",
                    confidence=0.93,
                    qualities={unmanaged.name: 0.91, managed.name: 0.88},
                )
            )

            result = resolve_archive_duplicates(
                archive,
                apply=False,
                include_near_duplicates=True,
                near_duplicate_client=client,
                near_duplicate_model="fake-ollama",
            )

            self.assertEqual(result.resolutions[0].keep_path.name, managed.name)

    def test_near_duplicate_resolution_uses_oldest_tiebreak_inside_quality_deadband(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            older = archive / "a-material.docx"
            newer = archive / "b-material.docx"
            write_docx(older, near_duplicate_text("Students copy the sentence."))
            write_docx(newer, near_duplicate_text("Students copy the sentences."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    older.name,
                    newer.name,
                    verdict="same_material",
                    confidence=0.93,
                    qualities={older.name: 0.88, newer.name: 0.89},
                )
            )
            first_seen = {
                older.name: datetime(2026, 5, 1, tzinfo=timezone.utc),
                newer.name: datetime(2026, 5, 2, tzinfo=timezone.utc),
            }

            with patch("wordbyword.class_archive.archive.file_first_seen_timestamp", side_effect=lambda path: first_seen[path.name]):
                result = resolve_archive_duplicates(
                    archive,
                    apply=False,
                    include_near_duplicates=True,
                    near_duplicate_client=client,
                    near_duplicate_model="fake-ollama",
                )

            self.assertEqual(result.resolutions[0].keep_path.name, older.name)

    def test_near_duplicate_related_review_is_reported_without_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, near_duplicate_text("Students complete page one."))
            write_docx(second, near_duplicate_text("Students complete page two."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    first.name,
                    second.name,
                    verdict="related_but_distinct",
                    confidence=0.87,
                    qualities={first.name: 0.80, second.name: 0.82},
                )
            )

            result = resolve_archive_duplicates(
                archive,
                apply=True,
                include_near_duplicates=True,
                near_duplicate_client=client,
                near_duplicate_model="fake-ollama",
            )

            self.assertEqual(result.duplicate_group_count, 0)
            self.assertEqual(len(result.near_duplicate_reviews), 1)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).exists())

    def test_near_duplicate_yes_applies_without_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            kept = archive / "material.docx"
            duplicate = archive / "material copy.docx"
            write_docx(kept, near_duplicate_text("Students finish the reading task."))
            write_docx(duplicate, near_duplicate_text("Students finish the reading tasks."))
            client = RoutingArchiveClient(
                near_duplicate_response=near_duplicate_response(
                    kept.name,
                    duplicate.name,
                    verdict="same_material",
                    confidence=0.91,
                    qualities={kept.name: 0.91, duplicate.name: 0.74},
                )
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
                patch("sys.stdin", io.StringIO("")),
                patch("wordbyword.cli.app.ollama_client", return_value=client),
                patch("wordbyword.cli.app.warn_if_model_unload_fails"),
            ):
                exit_code = main(["--resolve-archive-duplicates", str(archive), "--include-near-duplicates", "--yes"])

            self.assertEqual(exit_code, 0)
            self.assertTrue(kept.exists())
            self.assertFalse(duplicate.exists())
            self.assertNotIn("Apply this duplicate quarantine plan?", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_near_duplicate_candidate_cap_fails_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, near_duplicate_text("Students trace the word."))
            write_docx(second, near_duplicate_text("Students trace the words."))
            client = RoutingArchiveClient()

            with (
                patch("wordbyword.class_archive.archive.MAX_ARCHIVE_NEAR_DUPLICATE_CANDIDATES", 0),
                self.assertRaises(ClassSessionArchiveError) as context,
            ):
                resolve_archive_duplicates(
                    archive,
                    apply=False,
                    include_near_duplicates=True,
                    near_duplicate_client=client,
                    near_duplicate_model="fake-ollama",
                )

            self.assertIn("Too many suspected near-duplicate", str(context.exception))
            self.assertEqual(client.near_duplicate_calls, 0)

    def test_invalid_near_duplicate_model_response_fails_without_moving_files(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            first = archive / "material.docx"
            second = archive / "material copy.docx"
            write_docx(first, near_duplicate_text("Students review the sentence card."))
            write_docx(second, near_duplicate_text("Students review the sentence cards."))

            with self.assertRaises(ClassSessionArchiveError):
                resolve_archive_duplicates(
                    archive,
                    apply=True,
                    include_near_duplicates=True,
                    near_duplicate_client=InvalidJsonArchiveClient(),
                    near_duplicate_model="fake-ollama",
                )

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).exists())

    def test_quarantine_rolls_back_already_moved_files_when_later_move_fails(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            archive.mkdir()
            kept = archive / "a-material.docx"
            first_duplicate = archive / "b-material.docx"
            second_duplicate = archive / "c-material.docx"
            write_docx(kept, "중복된 수업 자료입니다.")
            shutil.copyfile(kept, first_duplicate)
            shutil.copyfile(kept, second_duplicate)
            original_rename = Path.rename

            def flaky_rename(source: Path, target: Path) -> None:
                source_is_archive_file = DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME not in source.parts
                if source.name == second_duplicate.name and source_is_archive_file:
                    raise OSError("simulated move failure")
                original_rename(source, target)

            with (
                patch("pathlib.Path.rename", new=flaky_rename),
                self.assertRaises(ClassSessionArchiveError) as context,
            ):
                resolve_archive_duplicates(
                    archive,
                    apply=True,
                    clock=fixed_archive_clock,
                )

            self.assertIn("Failed to resolve", str(context.exception))
            self.assertTrue(kept.exists())
            self.assertTrue(first_duplicate.exists())
            self.assertTrue(second_duplicate.exists())
            self.assertFalse((archive / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME / "manifest.json").exists())


class LiveLexiconUpdateTest(unittest.TestCase):
    def test_update_live_lexicon_seeds_canonical_and_supplemental_on_initial_run(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            canonical_dictionary_path = root / "build-dictionary" / "15_final_dictionary.jsonl"
            old_live_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon.jsonl"
            live_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-canonical.jsonl"
            supplemental_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-supplemental.jsonl"
            usage_candidates_path = root / "usage-signal-build" / "08_dictionary_candidates.jsonl"
            usage_summary_path = root / "usage-signal-build" / "00_summary.json"
            unmatched_output_path = root / "usage-signal-build" / "10_unmatched_usage_candidates.jsonl"
            old_live_lexicon_path.parent.mkdir(parents=True)
            old_live_lexicon_path.write_text("legacy live lexicon\n", encoding="utf-8")
            write_jsonl(
                canonical_dictionary_path,
                [
                    {
                        "canonical_key": "먹다",
                        "count": 48,
                        "document_count": 3,
                        "learning_signal_score": 0.4,
                        "roi_score": 0.5,
                        "curation_status": "confirmed",
                        "variants": ["먹다", "먹고"],
                        "example_replaced_with_cleaned_sentence": True,
                        "example_generated_with_ollama": False,
                        "meaning": "음식을 입을 통하여 배 속에 들여보내다.",
                        "meaning_generated_with_ollama": False,
                    },
                    {
                        "canonical_key": "물",
                        "count": 47,
                        "document_count": 3,
                        "learning_signal_score": 0.3,
                        "roi_score": 0.4,
                        "curation_status": "candidate",
                        "variants": ["물"],
                        "meaning": "투명한 액체.",
                    },
                ],
            )
            write_jsonl(
                usage_candidates_path,
                [
                    {
                        "canonical_key": "먹다",
                        "count": 7,
                        "document_count": 2,
                        "learning_signal_score": 0.3,
                        "variants": ["먹고", "먹어요", "먹다"],
                        "example": "사과를 먹고 학교에 갑니다.",
                    },
                    {
                        "canonical_key": "새단어",
                        "count": 3,
                        "document_count": 1,
                        "learning_signal_score": 0.112,
                        "example": "새단어를 배웠어요.",
                    },
                ],
            )
            usage_summary_path.write_text(json.dumps({"document_count": 4}), encoding="utf-8")

            result = update_live_lexicon(
                canonical_dictionary_path=canonical_dictionary_path,
                usage_candidates_path=usage_candidates_path,
                usage_summary_path=usage_summary_path,
                live_lexicon_path=live_lexicon_path,
                supplemental_lexicon_path=supplemental_lexicon_path,
                unmatched_usage_output_path=unmatched_output_path,
            )

            self.assertEqual(result.canonical_source, "seeded_from_dictionary")
            self.assertEqual(result.canonical_rows, 2)
            self.assertEqual(result.total_class_printouts, 4)
            self.assertEqual(result.matched_count, 1)
            self.assertEqual(result.unmatched_count, 1)
            self.assertEqual(result.supplemental_rows, 1)
            self.assertEqual(result.supplemental_active_rows, 1)
            self.assertEqual(result.supplemental_added_count, 1)
            updated_rows = read_jsonl(live_lexicon_path)
            self.assertEqual([row["canonical_key"] for row in updated_rows], ["먹다", "물"])
            self.assertNotIn("count", updated_rows[0])
            self.assertNotIn("document_count", updated_rows[0])
            self.assertNotIn("example_replaced_with_cleaned_sentence", updated_rows[0])
            self.assertNotIn("example_generated_with_ollama", updated_rows[0])
            self.assertNotIn("meaning_generated_with_ollama", updated_rows[0])
            self.assertEqual(updated_rows[0]["canonical_count"], 48)
            self.assertEqual(updated_rows[0]["canonical_document_count"], 3)
            self.assertNotIn("quality_score", updated_rows[0])
            self.assertEqual(updated_rows[0]["learning_signal_score"], 0.4)
            self.assertEqual(updated_rows[0]["curation_status"], "canonical")
            self.assertEqual(updated_rows[0]["class_count"], 7)
            self.assertEqual(updated_rows[0]["class_printout_count"], 2)
            self.assertEqual(updated_rows[0]["variants"], ["먹다", "먹고", "먹어요"])
            self.assertEqual(updated_rows[0]["roi_score"], 0.8)
            self.assertEqual(updated_rows[1]["canonical_count"], 47)
            self.assertEqual(updated_rows[1]["canonical_document_count"], 3)
            self.assertEqual(updated_rows[1]["learning_signal_score"], 0.3)
            self.assertEqual(updated_rows[1]["curation_status"], "canonical")
            self.assertEqual(updated_rows[1]["class_count"], 0)
            self.assertEqual(updated_rows[1]["class_printout_count"], 0)
            self.assertEqual(updated_rows[1]["roi_score"], 0.8)
            self.assertEqual(old_live_lexicon_path.read_text(encoding="utf-8"), "legacy live lexicon\n")
            unmatched_rows = read_jsonl(unmatched_output_path)
            self.assertEqual([row["canonical_key"] for row in unmatched_rows], ["새단어"])
            supplemental_rows = read_jsonl(supplemental_lexicon_path)
            self.assertEqual([row["canonical_key"] for row in supplemental_rows], ["새단어"])
            self.assertEqual(supplemental_rows[0]["class_count"], 3)
            self.assertEqual(supplemental_rows[0]["class_printout_count"], 1)
            self.assertEqual(supplemental_rows[0]["source_tier"], "supplemental_private")
            self.assertEqual(supplemental_rows[0]["canonical_status"], "non_canonical_observed")
            self.assertIs(supplemental_rows[0]["active"], True)
            self.assertNotIn("count", supplemental_rows[0])
            self.assertNotIn("document_count", supplemental_rows[0])

    def test_update_live_lexicon_maintains_existing_canonical_without_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            canonical_dictionary_path = root / "build-dictionary" / "15_final_dictionary.jsonl"
            live_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-canonical.jsonl"
            supplemental_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-supplemental.jsonl"
            usage_candidates_path = root / "usage-signal-build" / "08_dictionary_candidates.jsonl"
            usage_summary_path = root / "usage-signal-build" / "00_summary.json"
            unmatched_output_path = root / "usage-signal-build" / "10_unmatched_usage_candidates.jsonl"
            write_jsonl(
                live_lexicon_path,
                [
                    {
                        "canonical_key": "먹다",
                        "display": "먹다",
                        "canonical_count": 99,
                        "canonical_document_count": 9,
                        "learning_signal_score": 0.2,
                        "curation_status": "reviewed",
                        "variants": ["먹다"],
                        "meaning": "live meaning wins",
                        "manual_note": "keep me",
                        "class_count": 123,
                        "class_printout_count": 45,
                    },
                ],
            )
            usage_summary_path.parent.mkdir(parents=True, exist_ok=True)
            usage_summary_path.write_text(json.dumps({"document_count": 4}), encoding="utf-8")
            write_jsonl(
                usage_candidates_path,
                [
                    {
                        "canonical_key": "먹다",
                        "count": 7,
                        "document_count": 2,
                        "learning_signal_score": 0.16,
                        "variants": ["먹고"],
                    },
                ],
            )

            result = update_live_lexicon(
                canonical_dictionary_path=canonical_dictionary_path,
                usage_candidates_path=usage_candidates_path,
                usage_summary_path=usage_summary_path,
                live_lexicon_path=live_lexicon_path,
                supplemental_lexicon_path=supplemental_lexicon_path,
                unmatched_usage_output_path=unmatched_output_path,
            )

            self.assertEqual(result.canonical_source, "existing_live_lexicon")
            updated_rows = read_jsonl(live_lexicon_path)
            self.assertEqual(len(updated_rows), 1)
            self.assertEqual(updated_rows[0]["canonical_count"], 99)
            self.assertEqual(updated_rows[0]["canonical_document_count"], 9)
            self.assertEqual(updated_rows[0]["curation_status"], "reviewed")
            self.assertEqual(updated_rows[0]["meaning"], "live meaning wins")
            self.assertEqual(updated_rows[0]["manual_note"], "keep me")
            self.assertEqual(updated_rows[0]["class_count"], 7)
            self.assertEqual(updated_rows[0]["class_printout_count"], 2)
            self.assertEqual(updated_rows[0]["variants"], ["먹다", "먹고"])
            self.assertEqual(updated_rows[0]["roi_score"], 0.66)

    def test_update_live_lexicon_maintains_supplemental_rows(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            live_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-canonical.jsonl"
            supplemental_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-supplemental.jsonl"
            usage_candidates_path = root / "usage-signal-build" / "08_dictionary_candidates.jsonl"
            usage_summary_path = root / "usage-signal-build" / "00_summary.json"
            unmatched_output_path = root / "usage-signal-build" / "10_unmatched_usage_candidates.jsonl"
            write_jsonl(
                live_lexicon_path,
                [
                    {
                        "canonical_key": "물",
                        "canonical_count": 47,
                        "canonical_document_count": 3,
                        "learning_signal_score": 0.4,
                        "variants": ["물"],
                    },
                ],
            )
            write_jsonl(
                supplemental_lexicon_path,
                [
                    {
                        "canonical_key": "팀",
                        "display": "팀",
                        "variants": ["팀", "팀은"],
                        "learning_signal_score": 0.0,
                        "class_count": 99,
                        "class_printout_count": 99,
                        "source_tier": "supplemental_private",
                        "canonical_status": "non_canonical_observed",
                        "active": True,
                        "curation_status": "reviewed",
                        "manual_note": "keep",
                    },
                    {
                        "canonical_key": "사라짐",
                        "display": "사라짐",
                        "learning_signal_score": -0.1,
                        "class_count": 2,
                        "class_printout_count": 1,
                        "source_tier": "supplemental_private",
                        "canonical_status": "non_canonical_observed",
                        "active": True,
                    },
                    {
                        "canonical_key": "물",
                        "display": "물",
                        "learning_signal_score": 0.3,
                        "class_count": 4,
                        "class_printout_count": 2,
                        "source_tier": "supplemental_private",
                        "canonical_status": "non_canonical_observed",
                        "active": True,
                    },
                ],
            )
            usage_summary_path.parent.mkdir(parents=True, exist_ok=True)
            usage_summary_path.write_text(json.dumps({"document_count": 4}), encoding="utf-8")
            write_jsonl(
                usage_candidates_path,
                [
                    {
                        "canonical_key": "물",
                        "count": 7,
                        "document_count": 2,
                        "learning_signal_score": 0.1,
                        "variants": ["물을"],
                    },
                    {
                        "canonical_key": "팀",
                        "display": "팀",
                        "kind": "term",
                        "variants": ["팀", "팀을"],
                        "count": 4,
                        "document_count": 2,
                        "learning_signal_score": 0.32,
                        "example": "팀이 함께 실험했어요.",
                        "curation_status": "candidate",
                    },
                    {
                        "canonical_key": "새단어",
                        "display": "새단어",
                        "kind": "term",
                        "variants": ["새단어"],
                        "count": 3,
                        "document_count": 1,
                        "learning_signal_score": 0.2,
                        "example": "새단어를 배웠어요.",
                    },
                ],
            )

            result = update_live_lexicon(
                usage_candidates_path=usage_candidates_path,
                usage_summary_path=usage_summary_path,
                live_lexicon_path=live_lexicon_path,
                supplemental_lexicon_path=supplemental_lexicon_path,
                unmatched_usage_output_path=unmatched_output_path,
            )

            self.assertEqual(result.supplemental_rows, 4)
            self.assertEqual(result.supplemental_active_rows, 2)
            self.assertEqual(result.supplemental_added_count, 1)
            self.assertEqual(result.supplemental_updated_count, 1)
            self.assertEqual(result.supplemental_inactive_count, 1)
            self.assertEqual(result.supplemental_promoted_count, 1)
            self.assertEqual([row["canonical_key"] for row in read_jsonl(unmatched_output_path)], ["팀", "새단어"])
            supplemental_rows = read_jsonl(supplemental_lexicon_path)
            self.assertEqual([row["canonical_key"] for row in supplemental_rows], ["팀", "새단어", "물", "사라짐"])
            supplemental_by_key = {row["canonical_key"]: row for row in supplemental_rows}
            self.assertEqual(supplemental_by_key["팀"]["class_count"], 4)
            self.assertEqual(supplemental_by_key["팀"]["class_printout_count"], 2)
            self.assertNotIn("quality_score", supplemental_by_key["팀"])
            self.assertEqual(supplemental_by_key["팀"]["learning_signal_score"], 0.32)
            self.assertEqual(supplemental_by_key["팀"]["roi_score"], 0.82)
            self.assertEqual(supplemental_by_key["팀"]["variants"], ["팀", "팀은", "팀을"])
            self.assertEqual(supplemental_by_key["팀"]["manual_note"], "keep")
            self.assertEqual(supplemental_by_key["팀"]["curation_status"], "reviewed")
            self.assertEqual(supplemental_by_key["팀"]["canonical_status"], "non_canonical_observed")
            self.assertIs(supplemental_by_key["팀"]["active"], True)
            self.assertEqual(supplemental_by_key["사라짐"]["class_count"], 0)
            self.assertEqual(supplemental_by_key["사라짐"]["class_printout_count"], 0)
            self.assertEqual(supplemental_by_key["사라짐"]["canonical_status"], "inactive_observed")
            self.assertIs(supplemental_by_key["사라짐"]["active"], False)
            self.assertEqual(supplemental_by_key["물"]["canonical_status"], "promoted_to_canonical")
            self.assertIs(supplemental_by_key["물"]["active"], False)
            self.assertEqual(supplemental_by_key["새단어"]["source_tier"], "supplemental_private")
            self.assertIs(supplemental_by_key["새단어"]["active"], True)

    def test_update_live_lexicon_sorts_canonical_rows_by_roi(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            live_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-canonical.jsonl"
            supplemental_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-supplemental.jsonl"
            usage_candidates_path = root / "usage-signal-build" / "08_dictionary_candidates.jsonl"
            usage_summary_path = root / "usage-signal-build" / "00_summary.json"
            unmatched_output_path = root / "usage-signal-build" / "10_unmatched_usage_candidates.jsonl"
            write_jsonl(
                live_lexicon_path,
                [
                    {
                        "canonical_key": "낮다",
                        "canonical_count": 10,
                        "canonical_document_count": 1,
                        "learning_signal_score": -0.2,
                    },
                    {
                        "canonical_key": "높다",
                        "canonical_count": 10,
                        "canonical_document_count": 1,
                        "learning_signal_score": 0.4,
                    },
                    {
                        "canonical_key": "같다",
                        "canonical_count": 10,
                        "canonical_document_count": 1,
                        "learning_signal_score": 0.2,
                    },
                ],
            )
            usage_summary_path.parent.mkdir(parents=True, exist_ok=True)
            usage_summary_path.write_text(json.dumps({"document_count": 1}), encoding="utf-8")
            write_jsonl(usage_candidates_path, [])

            update_live_lexicon(
                usage_candidates_path=usage_candidates_path,
                usage_summary_path=usage_summary_path,
                live_lexicon_path=live_lexicon_path,
                supplemental_lexicon_path=supplemental_lexicon_path,
                unmatched_usage_output_path=unmatched_output_path,
            )

            rows = read_jsonl(live_lexicon_path)
            self.assertEqual([row["canonical_key"] for row in rows], ["높다", "같다", "낮다"])
            self.assertEqual([row["roi_score"] for row in rows], [0.9, 0.7, 0.3])

    def test_update_live_lexicon_regeneration_is_idempotent_for_same_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            canonical_dictionary_path = root / "build-dictionary" / "15_final_dictionary.jsonl"
            live_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-canonical.jsonl"
            supplemental_lexicon_path = root / "live-grade-lexicon" / "grade1-live-lexicon-supplemental.jsonl"
            usage_candidates_path = root / "usage-signal-build" / "08_dictionary_candidates.jsonl"
            usage_summary_path = root / "usage-signal-build" / "00_summary.json"
            unmatched_output_path = root / "usage-signal-build" / "10_unmatched_usage_candidates.jsonl"
            write_jsonl(
                canonical_dictionary_path,
                [
                    {
                        "canonical_key": "먹다",
                        "count": 48,
                        "document_count": 3,
                        "learning_signal_score": 0.427,
                        "roi_score": 0.5,
                    },
                ],
            )
            usage_summary_path.parent.mkdir(parents=True, exist_ok=True)
            usage_summary_path.write_text(json.dumps({"document_count": 2}), encoding="utf-8")
            write_jsonl(
                usage_candidates_path,
                [
                    {
                        "canonical_key": "먹다",
                        "count": 7,
                        "document_count": 2,
                        "learning_signal_score": 0.376,
                    },
                    {
                        "canonical_key": "새단어",
                        "count": 3,
                        "document_count": 1,
                        "learning_signal_score": 0.2,
                    },
                ],
            )

            update_live_lexicon(
                canonical_dictionary_path=canonical_dictionary_path,
                usage_candidates_path=usage_candidates_path,
                live_lexicon_path=live_lexicon_path,
                supplemental_lexicon_path=supplemental_lexicon_path,
                unmatched_usage_output_path=unmatched_output_path,
            )
            first_live = live_lexicon_path.read_text(encoding="utf-8")
            first_supplemental = supplemental_lexicon_path.read_text(encoding="utf-8")
            first_unmatched = unmatched_output_path.read_text(encoding="utf-8")
            update_live_lexicon(
                canonical_dictionary_path=canonical_dictionary_path,
                usage_candidates_path=usage_candidates_path,
                live_lexicon_path=live_lexicon_path,
                supplemental_lexicon_path=supplemental_lexicon_path,
                unmatched_usage_output_path=unmatched_output_path,
            )

            self.assertEqual(live_lexicon_path.read_text(encoding="utf-8"), first_live)
            self.assertEqual(supplemental_lexicon_path.read_text(encoding="utf-8"), first_supplemental)
            self.assertEqual(unmatched_output_path.read_text(encoding="utf-8"), first_unmatched)


class ClassSessionArchiveCliTest(unittest.TestCase):
    def test_parser_has_archive_rename_command(self) -> None:
        args = build_parser().parse_args(["--rename-class-session-archive"])

        self.assertEqual(
            args.rename_class_session_archive,
            Path("wbw-vocabulary-intelligence") / "class-session-archive",
        )
        self.assertEqual(args.rename_class_session_archive, DEFAULT_CLASS_SESSION_ARCHIVE_ROOT)
        self.assertFalse(args.apply_archive_renames)

    def test_parser_has_archive_duplicate_resolver_command(self) -> None:
        args = build_parser().parse_args(["--resolve-archive-duplicates"])

        self.assertEqual(
            args.resolve_archive_duplicates,
            Path("wbw-vocabulary-intelligence") / "class-session-archive",
        )
        self.assertEqual(args.resolve_archive_duplicates, DEFAULT_CLASS_SESSION_ARCHIVE_ROOT)
        self.assertFalse(args.yes)
        self.assertFalse(args.include_near_duplicates)

    def test_parser_has_near_duplicate_resolver_flag(self) -> None:
        args = build_parser().parse_args(["--resolve-archive-duplicates", "--include-near-duplicates"])

        self.assertEqual(args.resolve_archive_duplicates, DEFAULT_CLASS_SESSION_ARCHIVE_ROOT)
        self.assertTrue(args.include_near_duplicates)

    def test_yes_requires_archive_duplicate_resolver(self) -> None:
        stderr = io.StringIO()

        with (
            patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as context,
        ):
            main(["--build-usage-signals", "--yes"])

        self.assertEqual(context.exception.code, 2)
        self.assertIn("--yes requires --resolve-archive-duplicates", stderr.getvalue())

    def test_include_near_duplicates_requires_archive_duplicate_resolver(self) -> None:
        stderr = io.StringIO()

        with (
            patch("sys.stderr", stderr),
            self.assertRaises(SystemExit) as context,
        ):
            main(["--build-usage-signals", "--include-near-duplicates"])

        self.assertEqual(context.exception.code, 2)
        self.assertIn("--include-near-duplicates requires --resolve-archive-duplicates", stderr.getvalue())

    def test_main_with_archive_rename_skips_pipeline(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
            patch("wordbyword.cli.app.ollama_client", return_value=RecordingArchiveClient({})),
            patch("wordbyword.cli.app.rename_class_session_archive") as mock_rename,
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            mock_rename.return_value = FakeRenameResult()

            exit_code = main(["--rename-class-session-archive", "class-session-archive"])

        self.assertEqual(exit_code, 0)
        mock_pipeline.assert_not_called()
        self.assertIn("class-session archive rename complete", stdout.getvalue())

    def test_parser_has_usage_signal_defaults(self) -> None:
        args = build_parser().parse_args(["--build-usage-signals"])

        self.assertTrue(args.build_usage_signals)
        self.assertEqual(
            args.usage_signal_input_root,
            Path("wbw-vocabulary-intelligence") / "class-session-archive",
        )
        self.assertEqual(args.usage_signal_output_root, DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT)

    def test_parser_has_live_lexicon_update_defaults(self) -> None:
        args = build_parser().parse_args(["--update-live-lexicon"])

        self.assertTrue(args.update_live_lexicon)
        self.assertEqual(args.canonical_dictionary, DEFAULT_CANONICAL_DICTIONARY_PATH)
        self.assertEqual(args.usage_candidates, DEFAULT_USAGE_CANDIDATES_PATH)
        self.assertIsNone(args.usage_summary)
        self.assertEqual(args.live_lexicon, DEFAULT_LIVE_LEXICON_PATH)
        self.assertEqual(args.supplemental_lexicon, DEFAULT_SUPPLEMENTAL_LEXICON_PATH)
        self.assertEqual(args.unmatched_usage_output, DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH)

    def test_parser_has_live_lexicon_update_overrides(self) -> None:
        args = build_parser().parse_args(
            [
                "--update-live-lexicon",
                "--canonical-dictionary",
                "custom/15.jsonl",
                "--usage-candidates",
                "custom/08.jsonl",
                "--usage-summary",
                "custom/00.json",
                "--live-lexicon",
                "custom/live.jsonl",
                "--supplemental-lexicon",
                "custom/supplemental.jsonl",
            ]
        )

        self.assertEqual(args.canonical_dictionary, Path("custom/15.jsonl"))
        self.assertEqual(args.usage_candidates, Path("custom/08.jsonl"))
        self.assertEqual(args.usage_summary, Path("custom/00.json"))
        self.assertEqual(args.live_lexicon, Path("custom/live.jsonl"))
        self.assertEqual(args.supplemental_lexicon, Path("custom/supplemental.jsonl"))

    def test_main_with_usage_signal_build_skips_pipeline(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
            patch("wordbyword.cli.app.build_usage_signals") as mock_build,
        ):
            mock_build.return_value = FakeUsageSignalBuildResult()

            exit_code = main(["--build-usage-signals"])

        self.assertEqual(exit_code, 0)
        mock_pipeline.assert_not_called()
        self.assertIn("usage signals built", stdout.getvalue())

    def test_main_with_usage_signal_build_uses_interactive_summary_when_available(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
            patch("wordbyword.cli.app.build_usage_signals") as mock_build,
            patch("wordbyword.cli.app.emit_usage_signal_summary", return_value=True) as mock_summary,
        ):
            mock_build.return_value = FakeUsageSignalBuildResult()

            exit_code = main(["--build-usage-signals"])

        self.assertEqual(exit_code, 0)
        mock_pipeline.assert_not_called()
        mock_summary.assert_called_once_with(mock_build.return_value)
        self.assertNotIn("usage signals built", stdout.getvalue())

    def test_main_with_live_lexicon_update_skips_pipeline(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
            patch("wordbyword.cli.app.update_live_lexicon") as mock_update,
        ):
            mock_update.return_value = FakeLiveLexiconUpdateResult()

            exit_code = main(["--update-live-lexicon"])

        self.assertEqual(exit_code, 0)
        mock_pipeline.assert_not_called()
        self.assertIn("live lexicon updated", stdout.getvalue())

    def test_main_with_live_lexicon_update_uses_interactive_summary_when_available(self) -> None:
        stdout = io.StringIO()
        with (
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
            patch("wordbyword.cli.app.update_live_lexicon") as mock_update,
            patch("wordbyword.cli.app.emit_live_lexicon_summary", return_value=True) as mock_summary,
        ):
            mock_update.return_value = FakeLiveLexiconUpdateResult()

            exit_code = main(["--update-live-lexicon"])

        self.assertEqual(exit_code, 0)
        mock_pipeline.assert_not_called()
        mock_summary.assert_called_once_with(mock_update.return_value)
        self.assertNotIn("live lexicon updated", stdout.getvalue())


class RecordingArchiveClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[tuple[ChatMessage, ...], dict[str, object]]] = []

    def complete(
        self,
        messages: tuple[ChatMessage, ...] | list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        self.calls.append((tuple(messages), {"temperature": temperature, "json_mode": json_mode}))
        return CompletionResult(
            provider="ollama",
            model="fake-ollama",
            content=json.dumps(self.response, ensure_ascii=False),
        )


class RoutingArchiveClient:
    def __init__(
        self,
        near_duplicate_response: dict[str, object] | None = None,
        rename_response: dict[str, object] | None = None,
    ) -> None:
        self.near_duplicate_response = near_duplicate_response or {}
        self.rename_response = rename_response or {
            "grade_level": "grade1",
            "topic_slug": "reading",
            "short_title_slug": "practice",
            "korean_title": "읽기 자료",
            "confidence": 0.75,
        }
        self.calls: list[tuple[tuple[ChatMessage, ...], dict[str, object]]] = []
        self.near_duplicate_calls = 0
        self.rename_calls = 0

    def complete(
        self,
        messages: tuple[ChatMessage, ...] | list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        self.calls.append((tuple(messages), {"temperature": temperature, "json_mode": json_mode}))
        system_prompt = messages[0].content
        if "suspected duplicate" in system_prompt:
            self.near_duplicate_calls += 1
            response = self.near_duplicate_response
        else:
            self.rename_calls += 1
            response = self.rename_response
        return CompletionResult(
            provider="ollama",
            model="fake-ollama",
            content=json.dumps(response, ensure_ascii=False),
        )


class InvalidJsonArchiveClient:
    def complete(
        self,
        messages: tuple[ChatMessage, ...] | list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        return CompletionResult(
            provider="ollama",
            model="fake-ollama",
            content="not json",
        )


class FakeRenameResult:
    archive_root = DEFAULT_CLASS_SESSION_ARCHIVE_ROOT
    ledger_path = DEFAULT_CLASS_SESSION_ARCHIVE_ROOT / DEFAULT_ARCHIVE_LEDGER_FILENAME
    dry_run = True
    plans = ()
    planned_count = 0
    renamed_count = 0
    skipped_count = 0


class FakeUsageSignalBuildResult:
    archive_root = Path("wbw-vocabulary-intelligence") / "class-session-archive"
    output_root = DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT
    documents = 2
    extracted_pages = 2
    sentences = 3
    token_occurrences = 12
    vocabulary_terms = 7
    surface_words = 8


class FakeLiveLexiconUpdateResult:
    canonical_dictionary_path = DEFAULT_CANONICAL_DICTIONARY_PATH
    usage_candidates_path = DEFAULT_USAGE_CANDIDATES_PATH
    usage_summary_path = DEFAULT_USAGE_CANDIDATES_PATH.parent / "00_summary.json"
    live_lexicon_path = DEFAULT_LIVE_LEXICON_PATH
    supplemental_lexicon_path = DEFAULT_SUPPLEMENTAL_LEXICON_PATH
    unmatched_usage_output_path = DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH
    canonical_source = "existing_live_lexicon"
    canonical_rows = 2
    live_lexicon_rows = 2
    usage_candidate_rows = 3
    total_class_printouts = 2
    matched_count = 1
    unmatched_count = 2
    supplemental_rows = 2
    supplemental_active_rows = 1
    supplemental_added_count = 0
    supplemental_updated_count = 1
    supplemental_inactive_count = 1
    supplemental_promoted_count = 0


class InteractiveInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def fixed_archive_clock() -> datetime:
    return datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)


def duplicate_text() -> str:
    return (
        "This class handout explains apple reading practice for grade one students. "
        "Students read the same simple passage, notice key vocabulary, and answer gentle questions. "
        "The content is intentionally long enough for duplicate text fingerprinting. "
    ) * 3


def near_duplicate_text(variant_sentence: str) -> str:
    return (
        "This class handout asks grade one students to read a short classroom story about apples, "
        "notice repeated Korean vocabulary, circle the target words, answer two gentle comprehension "
        "questions, and talk with a partner about how the sentence changes. "
    ) * 3 + variant_sentence


def different_long_text() -> str:
    return (
        "This science worksheet explains shadows, sunlight, transparent objects, opaque objects, "
        "and how students can test a prediction with a flashlight during a classroom experiment. "
    ) * 5


def near_duplicate_response(
    first_name: str,
    second_name: str,
    *,
    verdict: str,
    confidence: float,
    qualities: dict[str, float],
) -> dict[str, object]:
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reason": "Model-reviewed duplicate relationship for test coverage.",
        "files": [
            {
                "filename": first_name,
                "content_quality": qualities[first_name],
                "reason": f"quality score for {first_name}",
            },
            {
                "filename": second_name,
                "content_quality": qualities[second_name],
                "reason": f"quality score for {second_name}",
            },
        ],
    }


def write_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def write_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 540, 760), text, fontsize=11)
    document.save(path)
    document.close()


def archive_call_filenames(client: RecordingArchiveClient) -> list[str]:
    filenames: list[str] = []
    for messages, _kwargs in client.calls:
        payload = json.loads(messages[1].content)
        filenames.append(str(payload["original_filename"]))
    return filenames


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
