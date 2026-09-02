from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from wordbyword.application.commands import CommandId, CommandResult
from wordbyword.application.events import event
from wordbyword.execution_guard import acquire_workspace_command_guard
from wordbyword.gui.bridge import (
    MAX_REQUEST_BYTES,
    BridgeError,
    _safe_payload,
    build_request,
    dispatch_request,
    read_request,
)
from wordbyword.gui.workspace import (
    MAX_PREVIEW_CHARS,
    WorkspaceError,
    dictionary_distribution,
    import_files,
    migration_preview,
    preview,
    snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_file(root: Path, relative: str, text: str = "saved content") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def inventory(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*") if not path.is_symlink()
    }


class DesktopWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_canonical_snapshot_counts_real_files_without_writing(self) -> None:
        source = write_file(self.root, "build-dictionary/original/grade6/source.txt")
        write_file(self.root, "build-dictionary/output/grade6/15_final_dictionary.jsonl", '{"display":"나무"}\n\n{}\n')
        write_file(self.root, "build-dictionary/output/grade6/00_summary.json", json.dumps({
            "document_count": 1, "extracted_page_count": 2, "sentence_count": 9,
        }))
        write_file(self.root, "classroom-printout-studio/class-source-materials/grade6/lesson.pdf")
        write_file(self.root, "classroom-printout-studio/printable-for-class/grade6-ko-a4-ollama/lesson/material.docx")
        write_file(self.root, "wbw-vocabulary-intelligence/class-session-archive/grade6/lesson.docx")
        before = inventory(self.root)

        result = snapshot(self.root, "grade6")

        self.assertEqual(inventory(self.root), before)
        self.assertFalse(result["legacy_layout"])
        self.assertEqual(result["counts"], {
            "sources": 1, "words": 2, "class_sources": 1, "handouts": 1, "archived": 1, "reports": 0,
        })
        self.assertEqual(result["summary"]["sentences"], 9)
        self.assertEqual(result["files"]["sources"][0]["path"], str(source))
        self.assertEqual(result["paths"], result["run_paths"])

    def test_legacy_snapshot_shows_files_without_moving_or_misassigning_them(self) -> None:
        write_file(self.root, "build-dictionary/original/old-book.txt")
        final = write_file(self.root, "build-dictionary/15_final_dictionary.jsonl", "{}\n{}\n")
        archive = write_file(self.root, "wbw-vocabulary-intelligence/class-session-archive/2026-05-01_grade6-lesson.docx")
        before = inventory(self.root)

        second_grade = snapshot(self.root, "grade2")
        sixth_grade = snapshot(self.root, "grade6")

        self.assertEqual(inventory(self.root), before)
        self.assertTrue(second_grade["legacy_layout"])
        self.assertEqual(second_grade["counts"]["words"], 2)
        self.assertEqual(second_grade["counts"]["archived"], 0)
        self.assertEqual(second_grade["paths"]["dictionary_output_dir"], str(final.parent))
        self.assertEqual(second_grade["run_paths"]["dictionary_output_dir"], str(self.root / "build-dictionary/output/grade2"))
        self.assertEqual(sixth_grade["counts"]["words"], 0)
        self.assertEqual(sixth_grade["files"]["archive"][0]["path"], str(archive))
        self.assertFalse((self.root / ".wordbyword").exists())

    def test_snapshot_warns_about_malformed_summary_and_ignores_hidden_sources(self) -> None:
        write_file(self.root, "build-dictionary/output/grade1/00_summary.json", "{writing")
        write_file(self.root, "build-dictionary/original/grade1/.private.txt")
        result = snapshot(self.root, "grade1")
        self.assertEqual(result["counts"]["sources"], 0)
        self.assertTrue(any("summary" in item for item in result["warnings"]))

    def test_preview_is_bounded_and_does_not_change_original(self) -> None:
        path = write_file(self.root, "build-dictionary/output/grade1/large.jsonl", "나" * (MAX_PREVIEW_CHARS + 20))
        before = path.read_bytes()
        result = preview(self.root, str(path), "grade1")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["text"]), MAX_PREVIEW_CHARS)
        self.assertIsNone(result["table"])
        self.assertEqual(path.read_bytes(), before)

    def test_csv_preview_returns_a_bounded_display_table(self) -> None:
        path = write_file(
            self.root,
            "build-dictionary/output/grade2/06_candidate_terms.csv",
            "canonical_key,count,example\n물,47,물이 흐릅니다.\n나무,22,나무가 자랍니다.\n",
        )

        result = preview(self.root, str(path), "grade2")

        self.assertEqual(result["table"], {
            "format": "CSV",
            "columns": ["Canonical key", "Occurrences", "Example sentence"],
            "rows": [["물", "47", "물이 흐릅니다."], ["나무", "22", "나무가 자랍니다."]],
            "truncated": False,
        })

    def test_jsonl_preview_keeps_only_stage_specific_decision_fields(self) -> None:
        path = write_file(
            self.root,
            "build-dictionary/output/grade2/08_dictionary_candidates.jsonl",
            "\n".join([
                json.dumps({
                    "canonical_key": "물", "display": "물", "count": 47,
                    "learning_signal_score": 0.8, "variants": ["물", "물이"],
                    "example": "물이 흐릅니다.", "curation_status": "ready",
                }, ensure_ascii=False),
                json.dumps({
                    "canonical_key": "나무", "display": "나무", "count": 22,
                    "learning_signal_score": 0.5, "example": "나무가 자랍니다.",
                    "curation_status": "review",
                }, ensure_ascii=False),
            ]) + "\n",
        )

        table = preview(self.root, str(path), "grade2")["table"]

        self.assertEqual(table["format"], "JSONL")
        self.assertEqual(
            table["columns"],
            ["Canonical key", "Learning signal", "Occurrences", "Example sentence"],
        )
        self.assertEqual(table["rows"][0], ["물", "0.8", "47", "물이 흐릅니다."])
        self.assertEqual(table["rows"][1], ["나무", "0.5", "22", "나무가 자랍니다."])

    def test_cleaned_sentence_preview_compares_only_the_judgment_fields(self) -> None:
        path = write_file(
            self.root,
            "build-dictionary/output/grade2/10_cleaned_example_sentences.csv",
            "document,page,source_sentence,cleaned_sentence,cleanup_action,cleanup_reason,"
            "model_confidence,source_similarity,cleaned_quality_score\n"
            "교과서.pdf,12,학교에 갑니다.,학교에 가요.,rewrite,말투 정리,0.94,0.83,0.91\n",
        )

        table = preview(self.root, str(path), "grade2")["table"]

        self.assertEqual(
            table["columns"],
            ["Original sentence", "Cleaned sentence", "Cleanup decision", "Cleaned quality"],
        )
        self.assertEqual(table["rows"], [["학교에 갑니다.", "학교에 가요.", "rewrite", "0.91"]])

    def test_final_dictionary_preview_prefers_roi_but_uses_learning_signal_fallback(self) -> None:
        roi_path = write_file(
            self.root,
            "build-dictionary/output/grade2/15_final_dictionary.jsonl",
            json.dumps({
                "canonical_key": "물", "meaning": "마시는 액체", "example": "물을 마셔요.",
                "roi_score": 0.92, "learning_signal_score": 0.71, "count": 47,
            }, ensure_ascii=False) + "\n",
        )
        table = preview(self.root, str(roi_path), "grade2")["table"]
        self.assertEqual(
            table["columns"],
            ["Canonical key", "Meaning", "Example sentence", "ROI score"],
        )
        self.assertEqual(table["rows"], [["물", "마시는 액체", "물을 마셔요.", "0.92"]])

        learning_path = write_file(
            self.root,
            "build-dictionary/output/grade2/14_vocabulary_with_meanings.jsonl",
            json.dumps({
                "canonical_key": "나무", "meaning": "줄기가 있는 식물", "example": "나무가 자라요.",
                "learning_signal_score": 0.64, "count": 22,
            }, ensure_ascii=False) + "\n",
        )
        table = preview(self.root, str(learning_path), "grade2")["table"]
        self.assertEqual(table["columns"][-1], "Learning signal")
        self.assertEqual(table["rows"][0][-1], "0.64")

    def test_json_object_preview_uses_field_and_value_columns(self) -> None:
        path = write_file(
            self.root,
            "build-dictionary/output/grade2/00_summary.json",
            json.dumps({"document_count": 3, "stages": ["00", "01"]}),
        )

        table = preview(self.root, str(path), "grade2")["table"]

        self.assertEqual(table["columns"], ["Field", "Value"])
        self.assertEqual(table["rows"], [["document_count", "3"], ["stages", '["00","01"]']])

    def test_pdf_preview_exposes_metadata_without_reading_or_changing_content(self) -> None:
        path = write_file(self.root, "build-dictionary/original/grade1/자료.PDF", "%PDF-1.7\n")
        before = inventory(self.root)
        with patch.object(Path, "open", side_effect=AssertionError("PDF content must not be read")):
            result = preview(self.root, str(path), "grade1")
        self.assertEqual(result["byte_count"], path.stat().st_size)
        self.assertEqual(result["text"], "PDF document · 9 bytes\n\nOpen the original file to view its pages.")
        self.assertEqual(inventory(self.root), before)

    def test_preview_rejects_traversal_hidden_code_and_escaping_symlinks(self) -> None:
        for relative in ("build-dictionary/.private.json", "build-dictionary/script.py"):
            path = write_file(self.root, relative)
            with self.subTest(relative=relative), self.assertRaises(WorkspaceError):
                preview(self.root, str(path))
        with self.assertRaises(WorkspaceError):
            preview(self.root, "build-dictionary/../README.md")
        with tempfile.TemporaryDirectory() as external:
            secret = write_file(Path(external), "secret.txt")
            link = self.root / "build-dictionary/link.txt"
            link.symlink_to(secret)
            with self.assertRaises(WorkspaceError):
                preview(self.root, str(link))

    def test_docx_preview_reads_paragraphs_without_extracting_files(self) -> None:
        document = self.root / "classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama/material.docx"
        document.parent.mkdir(parents=True)
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>안녕하세요</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p></w:body></w:document>'
            ))
        before = inventory(self.root)
        result = preview(self.root, str(document))
        self.assertEqual(result["text"], "안녕하세요\nSecond paragraph")
        self.assertEqual(inventory(self.root), before)

    def test_dictionary_distribution_returns_compact_chart_bins_without_writing(self) -> None:
        artifact = write_file(
            self.root,
            "build-dictionary/output/grade4/15_final_dictionary.jsonl",
            "\n".join([
                json.dumps({"learning_signal_score": -0.8, "count": 1}),
                json.dumps({"learning_signal_score": 0.0, "count": 10}),
                json.dumps({"learning_signal_score": 0.9, "count": 100}),
                json.dumps({"quality_score": 0.25, "count": 0}),
            ]) + "\n",
        )
        before = inventory(self.root)

        result = dictionary_distribution(self.root, str(artifact), "grade4")

        self.assertEqual(inventory(self.root), before)
        self.assertEqual(result["artifact_id"], "final_dictionary")
        self.assertEqual(result["stage"], "15")
        self.assertEqual(result["entry_count"], 4)
        learning = result["learning_signal"]
        occurrence = result["occurrence_count"]
        self.assertEqual(sum(learning["bins"]), 4)
        self.assertEqual(learning["bins"][0], 1)
        self.assertEqual(learning["bins"][6], 1)
        self.assertEqual(learning["bins"][12], 1)
        self.assertEqual(learning["bins"][23], 1)
        self.assertEqual(sum(occurrence["bins"]), 4)
        self.assertEqual(occurrence["bins"][0], 2)
        self.assertEqual(occurrence["bins"][11], 1)
        self.assertEqual(occurrence["bins"][23], 1)
        self.assertEqual(occurrence["axis_labels"], ["1", "10", "100"])

    def test_dictionary_distribution_rejects_unknown_corrupt_and_escaping_artifacts(self) -> None:
        unknown = write_file(self.root, "build-dictionary/output/grade1/other.jsonl", "{}\n")
        corrupt = write_file(self.root, "build-dictionary/output/grade1/15_final_dictionary.jsonl", "{broken\n")
        for value, message in [
            (str(unknown), "stages 08 or 11–15"),
            (str(corrupt), "not valid JSON"),
            (str(self.root.parent / "outside.jsonl"), "inside this workspace"),
        ]:
            with self.subTest(value=value), self.assertRaisesRegex(WorkspaceError, message):
                dictionary_distribution(self.root, value, "grade1")

    def test_migration_preview_is_read_only_and_identifies_move(self) -> None:
        write_file(self.root, "build-dictionary/15_final_dictionary.jsonl", "{}\n")
        before = inventory(self.root)
        result = migration_preview(self.root)
        self.assertTrue(result["can_apply"])
        self.assertEqual(result["planned_count"], 1)
        self.assertIn("build-dictionary/output/grade2/15_final_dictionary.jsonl", result["text"])
        self.assertEqual(inventory(self.root), before)

    def test_migration_preview_rejects_symlink_source(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            source = write_file(Path(external), "outside.jsonl", "{}\n")
            (self.root / "build-dictionary").mkdir()
            (self.root / "build-dictionary/15_final_dictionary.jsonl").symlink_to(source)
            result = migration_preview(self.root)
            self.assertFalse(result["can_apply"])
            self.assertIn("Symbolic links", result["text"])


class DesktopImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.external = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.external.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.external_root = Path(self.external.name).resolve()

    def test_import_copies_to_canonical_grade_folder_and_preserves_original(self) -> None:
        source = write_file(self.external_root, "lesson.txt", "원본")
        result = import_files(self.root, "grade8", "sources", [str(source)])
        imported = self.root / "build-dictionary/original/grade8/lesson.txt"
        self.assertEqual(result, {"file_1": str(imported)})
        self.assertEqual(imported.read_text(), "원본")
        self.assertEqual(source.read_text(), "원본")

    def test_collision_preflights_entire_batch_without_overwriting(self) -> None:
        first = write_file(self.external_root, "new.txt", "new")
        second = write_file(self.external_root, "same.txt", "replacement")
        existing = write_file(self.root, "build-dictionary/original/grade1/same.txt", "original")
        before = inventory(self.root)
        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            import_files(self.root, "grade1", "sources", [str(first), str(second)])
        self.assertEqual(inventory(self.root), before)
        self.assertEqual(existing.read_text(), "original")

    def test_import_rejects_symlinks_hidden_files_and_traversal(self) -> None:
        source = write_file(self.external_root, "lesson.txt")
        link = self.external_root / "link.txt"
        link.symlink_to(source)
        hidden = write_file(self.external_root, ".hidden.txt")
        for value in (str(link), str(hidden), str(self.external_root / "directory/../lesson.txt")):
            with self.subTest(value=value), self.assertRaises(WorkspaceError):
                import_files(self.root, "grade1", "sources", [value])
        self.assertEqual(inventory(self.root), {})

    def test_import_blocks_legacy_layout_until_explicit_migration(self) -> None:
        write_file(self.root, "build-dictionary/15_final_dictionary.jsonl", "{}\n")
        source = write_file(self.external_root, "lesson.txt")
        before = inventory(self.root)
        with self.assertRaisesRegex(WorkspaceError, "migration"):
            import_files(self.root, "grade2", "sources", [str(source)])
        self.assertEqual(inventory(self.root), before)

    def test_import_rejects_escaping_destination(self) -> None:
        source = write_file(self.external_root, "lesson.txt")
        target = self.root / "build-dictionary/original/grade1"
        target.parent.mkdir(parents=True)
        target.symlink_to(self.external_root, target_is_directory=True)
        with self.assertRaises(WorkspaceError):
            import_files(self.root, "grade1", "sources", [str(source)])
        self.assertEqual(source.read_text(), "saved content")

    def test_classroom_import_accepts_only_fully_supported_pdf_sources(self) -> None:
        document = write_file(self.external_root, "lesson.txt")
        with self.assertRaisesRegex(WorkspaceError, "Supported: .pdf"):
            import_files(self.root, "grade1", "class_sources", [str(document)])
        pdf = write_file(self.external_root, "lesson.pdf", "%PDF-test")
        result = import_files(self.root, "grade1", "class_sources", [str(pdf)])
        self.assertEqual(Path(result["file_1"]).read_bytes(), pdf.read_bytes())

    def test_failed_copy_removes_only_new_files_and_directories(self) -> None:
        source = write_file(self.external_root, "lesson.txt")
        with patch("wordbyword.gui.workspace.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                import_files(self.root, "grade1", "sources", [str(source)])
        self.assertEqual(inventory(self.root), {})
        self.assertTrue(source.exists())


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.packets: list[dict[str, object]] = []

    def dispatch(self, request: dict[str, object]) -> int:
        return dispatch_request(self.root, request, self.packets.append)

    def test_request_reader_rejects_malformed_oversized_multiple_and_duplicate_json(self) -> None:
        values = [
            b"not json", b"[]", b"{}\n{}", b'{"operation":"snapshot","operation":"run"}',
            b'{"operation":"snapshot","value":NaN}', b" " * (MAX_REQUEST_BYTES + 1),
        ]
        for value in values:
            with self.subTest(value=value[:80]), self.assertRaises(BridgeError):
                read_request(io.BytesIO(value))

    def test_invalid_operations_and_payloads_return_error_packets(self) -> None:
        for request in ({}, {"operation": "unknown"}, {"operation": "run", "arguments": "--help"}, {"operation": "snapshot", "grade": "grade99"}):
            with self.subTest(request=request):
                self.packets.clear()
                self.assertEqual(self.dispatch(request), 2)
                self.assertEqual(self.packets[-1]["type"], "error")

    def test_run_uses_existing_validation_and_requires_explicit_grade(self) -> None:
        with patch("wordbyword.application.use_cases.run_request") as run:
            self.assertEqual(self.dispatch({"operation": "run", "arguments": ["--prep-dictionary"]}), 2)
            run.assert_not_called()
        self.assertIn("--target-grade", self.packets[-1]["message"])

    def test_run_rejects_multiple_commands_even_when_old_parser_accepts_combination(self) -> None:
        with self.assertRaisesRegex(BridgeError, "exactly one"):
            build_request(["--link-cleaned-examples", "--add-reference-meanings", "--target-grade", "grade1"])
        request = build_request(["--clean-example-sentences", "--clean-dictionary-candidates", "--target-grade", "grade1"])
        self.assertEqual(request.command_id, CommandId.DICTIONARY_RECOVERY)

    def test_run_dispatches_typed_request_under_guard_and_streams_result(self) -> None:
        def run(request: object, emit_event: object) -> CommandResult:
            self.assertEqual(request.command_id, CommandId.DICTIONARY_PREP)
            self.assertEqual(Path.cwd(), self.root)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                acquire_workspace_command_guard("competing CLI", workspace_root=self.root)
            emit_event(event("dictionary", "run", "Working", current=1, total=2))
            return CommandResult(0, "complete", "Prepared.", {"output_dir": "build-dictionary/output/grade1"})

        original_cwd = Path.cwd()
        with patch("wordbyword.application.use_cases.run_request", side_effect=run):
            code = self.dispatch({"operation": "run", "arguments": ["--prep-dictionary", "--target-grade", "grade1"]})
        self.assertEqual(code, 0)
        self.assertEqual([packet["type"] for packet in self.packets], ["event", "result"])
        self.assertEqual(self.packets[-1]["data"]["exit_code"], 0)
        self.assertEqual(Path.cwd(), original_cwd)
        with acquire_workspace_command_guard("later CLI", workspace_root=self.root):
            pass

    def test_grade_two_desktop_build_accepts_original_textbook_filenames(self) -> None:
        source = write_file(self.root, "build-dictionary/original/grade2/2-1_국어[나].pdf", "%PDF-test")

        def run(request: object, emit_event: object) -> CommandResult:
            self.assertEqual(request.command_id, CommandId.DICTIONARY_PREP)
            self.assertEqual(request.target_grade, "grade2")
            self.assertFalse(request.enforce_managed_sources)
            self.assertEqual(request.input_dir.resolve(), source.parent.resolve())
            return CommandResult(0, "complete", "Prepared.")

        with patch("wordbyword.application.use_cases.run_request", side_effect=run):
            code = self.dispatch({
                "operation": "run",
                "arguments": ["--prep-dictionary", "--target-grade", "grade2"],
            })

        self.assertEqual(code, 0)
        self.assertEqual(self.packets[-1]["data"]["exit_code"], 0)
        self.assertEqual(source.name, "2-1_국어[나].pdf")

    def test_run_reports_lock_conflict_without_executing(self) -> None:
        with acquire_workspace_command_guard("CLI already running", workspace_root=self.root):
            with patch("wordbyword.application.use_cases.run_request") as run:
                code = self.dispatch({"operation": "run", "arguments": ["--prep-dictionary", "--target-grade", "grade1"]})
                run.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn("already running", self.packets[-1]["message"])

    def test_subprocess_respects_lock_held_by_another_process(self) -> None:
        with acquire_workspace_command_guard("CLI already running", workspace_root=self.root):
            result = subprocess.run(
                [sys.executable, "-B", "-m", "wordbyword.gui.bridge", "--workspace", str(self.root)],
                input=json.dumps({"operation": "run", "arguments": ["--prep-dictionary", "--target-grade", "grade1"]}),
                text=True, capture_output=True, cwd=PROJECT_ROOT, timeout=30, check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("already running", json.loads(result.stdout)["message"])

    def test_real_sigint_produces_final_result_and_releases_workspace_lock(self) -> None:
        script = (
            "import sys,time\n"
            "from wordbyword.application import use_cases\n"
            "from wordbyword.gui import bridge\n"
            "def slow(request,emit_event):\n"
            " time.sleep(30)\n"
            "use_cases._run_request=slow\n"
            "raise SystemExit(bridge.main(['--workspace',sys.argv[1]]))\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=PROJECT_ROOT,
        )
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({"operation": "run", "arguments": ["--prep-dictionary", "--target-grade", "grade1"]}))
        process.stdin.close()
        process.stdin = None
        first = json.loads(process.stdout.readline())
        self.assertEqual(first["type"], "event")
        self.assertEqual(first["data"]["phase"], "start")
        process.send_signal(signal.SIGINT)
        output, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, errors)
        packets = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(packets[-1]["type"], "result")
        self.assertEqual(packets[-1]["data"]["exit_code"], 130)
        with acquire_workspace_command_guard("after real interruption", workspace_root=self.root):
            pass

    def test_run_rejects_outside_output_and_legacy_mutation(self) -> None:
        with patch("wordbyword.application.use_cases.run_request") as run:
            code = self.dispatch({"operation": "run", "arguments": [
                "--prep-dictionary", "--target-grade", "grade2", "--output", str(self.root.parent / "outside"),
            ]})
            self.assertEqual(code, 2)
            write_file(self.root, "build-dictionary/15_final_dictionary.jsonl", "{}\n")
            self.assertEqual(self.dispatch({"operation": "run", "arguments": ["--prep-dictionary", "--target-grade", "grade2"]}), 2)
            run.assert_not_called()

    def test_keyboard_interrupt_returns_cancelled_result_and_releases_lock(self) -> None:
        with patch("wordbyword.application.use_cases.run_request", side_effect=KeyboardInterrupt):
            code = self.dispatch({"operation": "run", "arguments": ["--prep-dictionary", "--target-grade", "grade1"]})
        self.assertEqual(code, 130)
        self.assertEqual(self.packets[-1]["data"]["status"], "cancelled")
        with acquire_workspace_command_guard("after interruption", workspace_root=self.root):
            pass

    def test_import_uses_same_guard_and_emits_standard_result(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            source = write_file(Path(external).resolve(), "lesson.txt")
            code = self.dispatch({"operation": "import", "grade": "grade3", "destination": "sources", "files": [str(source)]})
        self.assertEqual(code, 0)
        self.assertEqual(self.packets[-1]["type"], "result")
        self.assertEqual(self.packets[-1]["data"]["exit_code"], 0)
        self.assertTrue((self.root / "build-dictionary/original/grade3/lesson.txt").exists())

    def test_distribution_is_a_read_only_terminal_bridge_operation(self) -> None:
        artifact = write_file(
            self.root, "build-dictionary/output/grade3/15_final_dictionary.jsonl",
            json.dumps({"learning_signal_score": 0.1, "count": 7}) + "\n",
        )
        with acquire_workspace_command_guard("another reader", workspace_root=self.root):
            before = inventory(self.root)
            code = self.dispatch({"operation": "distribution", "grade": "grade3", "path": str(artifact)})
            self.assertEqual(inventory(self.root), before)
        self.assertEqual(code, 0)
        self.assertEqual([packet["type"] for packet in self.packets], ["distribution"])
        self.assertEqual(self.packets[0]["data"]["entry_count"], 1)
        self.assertEqual(self.packets[0]["data"]["path"], str(artifact))

    def test_protocol_subprocess_snapshot_is_json_and_read_only(self) -> None:
        before = inventory(self.root)
        result = subprocess.run(
            [sys.executable, "-B", "-m", "wordbyword.gui.bridge", "--workspace", str(self.root)],
            input='{"operation":"snapshot","grade":"grade1"}', text=True,
            capture_output=True, cwd=PROJECT_ROOT, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        packets = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([packet["type"] for packet in packets], ["snapshot"])
        self.assertEqual(inventory(self.root), before)

    def test_protocol_redirects_python_and_native_stdout_and_redacts_secrets(self) -> None:
        script = (
            "import atexit,os,sys\n"
            "from wordbyword.gui import bridge\n"
            "atexit.register(lambda: print('late diagnostic'))\n"
            "def fake(root,request,emit):\n"
            " print('python diagnostic')\n"
            " os.write(1,b'native diagnostic\\n')\n"
            " emit({'type':'error','message':os.environ['OPENAI_API_KEY']})\n"
            " return 2\n"
            "bridge.dispatch_request=fake\n"
            "raise SystemExit(bridge.main(['--workspace',sys.argv[1]]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.root)], input='{"operation":"snapshot"}',
            text=True, capture_output=True, cwd=PROJECT_ROOT, timeout=30, check=False,
            env={**os.environ, "OPENAI_API_KEY": "desktop-test-secret-do-not-display"},
        )
        self.assertEqual(json.loads(result.stdout), {"type": "error", "message": "[redacted]"})
        self.assertIn("python diagnostic", result.stderr)
        self.assertIn("native diagnostic", result.stderr)
        self.assertIn("late diagnostic", result.stderr)
        self.assertNotIn("desktop-test-secret", result.stdout)

    def test_sensitive_arguments_are_rejected_and_nested_output_is_redacted(self) -> None:
        with self.assertRaisesRegex(BridgeError, "credentials"):
            build_request(["--parse-source-materials", "--target-grade", "grade1", "--paddleocr-vl-rec-api-key", "secret"])
        with patch.dict(os.environ, {"OPENAI_API_KEY": "desktop-test-secret"}):
            self.assertEqual(_safe_payload({"data": ["failed: desktop-test-secret"]}), {"data": ["failed: [redacted]"]})


if __name__ == "__main__":
    unittest.main()
