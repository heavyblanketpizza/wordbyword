from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from docx import Document

from wordbyword.class_archive.archive import (
    DEFAULT_ARCHIVE_LEDGER_FILENAME,
    DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
    ArchiveRenameMetadata,
    ClassSessionArchiveError,
    archive_filename,
    build_archived_material,
    discover_archive_materials,
    discover_archive_material_paths,
    normalize_grade_level,
    rename_class_session_archive,
    safe_slug,
)
from wordbyword.class_archive.usage_signals import (
    DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT,
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


class ClassSessionArchiveCliTest(unittest.TestCase):
    def test_parser_has_archive_rename_command(self) -> None:
        args = build_parser().parse_args(["--rename-class-session-archive"])

        self.assertEqual(
            args.rename_class_session_archive,
            Path("wbw-vocabulary-intelligence") / "class-session-archive",
        )
        self.assertEqual(args.rename_class_session_archive, DEFAULT_CLASS_SESSION_ARCHIVE_ROOT)
        self.assertFalse(args.apply_archive_renames)

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


def write_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def write_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def archive_call_filenames(client: RecordingArchiveClient) -> list[str]:
    filenames: list[str] = []
    for messages, _kwargs in client.calls:
        payload = json.loads(messages[1].content)
        filenames.append(str(payload["original_filename"]))
    return filenames
