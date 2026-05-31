from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wordbyword.application.commands import (
    ArchiveRenameRequest,
    CommandId,
    CommandResult,
    DictionaryBuildRequest,
    ShowDistributionRequest,
    command_spec,
    registered_command_ids,
)
from wordbyword.application.use_cases import run_request
from wordbyword.cli import build_parser
from wordbyword.cli.request_builder import build_cli_request, command_id_from_cli_args
from wordbyword.dictionary_build.pipeline import PipelineResult
from wordbyword.gui.request_builder import build_gui_request, gui_workflow_command_ids


class ApplicationCommandRegistryTests(unittest.TestCase):
    def test_all_gui_workflow_ids_resolve_to_registered_specs(self) -> None:
        registered = set(registered_command_ids())

        self.assertEqual(
            gui_workflow_command_ids(),
            {
                "dictionary_build": CommandId.DICTIONARY_BUILD,
                "dictionary_prep": CommandId.DICTIONARY_PREP,
                "class_printouts": CommandId.CLASS_PRINTOUTS,
                "archive_rename_preview": CommandId.ARCHIVE_RENAME_PREVIEW,
                "archive_rename_apply": CommandId.ARCHIVE_RENAME_APPLY,
                "archive_duplicates_preview": CommandId.ARCHIVE_DUPLICATES_PREVIEW,
                "archive_duplicates_apply": CommandId.ARCHIVE_DUPLICATES_APPLY,
                "usage_signals": CommandId.USAGE_SIGNALS,
                "live_lexicon": CommandId.LIVE_LEXICON,
                "after_class_report": CommandId.AFTER_CLASS_REPORT,
            },
        )
        self.assertLessEqual(set(gui_workflow_command_ids().values()), registered)

    def test_cli_commands_have_existing_lock_labels(self) -> None:
        cases = {
            ("--show-distribution",): None,
            ("--migrate-grade-workspaces",): "grade workspace migration",
            ("--build-dictionary",): "dictionary build",
            ("--prep-dictionary",): "dictionary prep",
            ("--clean-example-sentences",): "dictionary recovery",
            ("--parse-source-material", "lesson.pdf"): "source material parse",
            ("--parse-source-materials", "sources"): "source materials parse",
            ("--rewrite-whole-source-material", "parsed/lesson"): "whole source material rewrite",
            ("--rewrite-whole-source-materials", "parsed"): "whole source materials rewrite",
            ("--rewrite-vocabulary-guided-source-material", "parsed/lesson"): "vocabulary-guided source material rewrite",
            ("--rewrite-vocabulary-guided-source-materials", "parsed"): "vocabulary-guided source materials rewrite",
            ("--print-class-material", "rewritten/lesson"): "class material print",
            ("--print-class-materials", "rewritten"): "class materials print",
            ("--build-class-printouts", "sources"): "class printout build",
            ("--generate-after-class-reading-report", "archive/lesson.docx"): "after-class reading report",
            ("--resolve-archive-duplicates", "archive"): "archive duplicate resolution",
            ("--resolve-dictionary-source-duplicates", "original"): "dictionary source duplicate resolution",
            ("--rename-class-session-archive", "archive"): "class session archive rename",
            ("--rename-dictionary-source-archive", "original"): "dictionary source archive rename",
            ("--build-usage-signals",): "usage signal build",
            ("--update-live-lexicon",): "live lexicon update",
            ("--link-cleaned-examples",): "cleaned example linking",
            ("--add-reference-meanings",): "reference meaning assignment",
            ("--generate-missing-meanings",): "missing meaning generation",
        }
        parser = build_parser()

        for argv, expected_label in cases.items():
            with self.subTest(argv=argv):
                args = parser.parse_args([*argv, "--target-grade", "grade1"] if "--show-distribution" not in argv else list(argv))
                command_id = command_id_from_cli_args(args)

                self.assertIsNotNone(command_id)
                self.assertEqual(command_spec(command_id).lock_label, expected_label)

    def test_gui_modules_do_not_import_cli_app(self) -> None:
        gui_root = Path(__file__).resolve().parents[1] / "wordbyword" / "gui"
        offenders: list[Path] = []
        for path in gui_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "wordbyword.cli.app":
                    offenders.append(path)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "wordbyword.cli.app":
                            offenders.append(path)

        self.assertEqual(offenders, [])

    def test_application_commands_import_does_not_load_adapters(self) -> None:
        code = (
            "import sys\n"
            "import wordbyword.application.commands\n"
            "unexpected = [name for name in ('wordbyword.cli.app', 'wordbyword.gui.workflows') if name in sys.modules]\n"
            "if unexpected:\n"
            "    raise SystemExit(', '.join(unexpected))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_application_use_cases_do_not_import_adapters(self) -> None:
        code = (
            "import sys\n"
            "import wordbyword.application.use_cases\n"
            "unexpected = [name for name in ('wordbyword.cli.app', 'wordbyword.gui.workflows') if name in sys.modules]\n"
            "if unexpected:\n"
            "    raise SystemExit(', '.join(unexpected))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cli_and_gui_adapters_do_not_import_domain_workflows(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        adapter_paths = [
            project_root / "wordbyword" / "cli" / "app.py",
            project_root / "wordbyword" / "gui" / "workflows.py",
        ]
        forbidden_prefixes = (
            "wordbyword.after_class_reports",
            "wordbyword.class_archive",
            "wordbyword.classroom_printout_studio",
            "wordbyword.dictionary_build",
        )
        offenders: list[tuple[Path, str]] = []
        for path in adapter_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith(forbidden_prefixes):
                        offenders.append((path, node.module))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(forbidden_prefixes):
                            offenders.append((path, alias.name))

        self.assertEqual(offenders, [])


class ApplicationCommandAdapterTests(unittest.TestCase):
    def test_dictionary_cli_and_gui_requests_match(self) -> None:
        parser = build_parser()
        cli_args = parser.parse_args(
            [
                "--build-dictionary",
                "--target-grade",
                "grade1",
                "--input",
                "input",
                "--output",
                "output",
                "--reference-dictionary",
                "reference.jsonl",
                "--ollama-model",
                "gemma-test",
                "--include-numbers",
            ]
        )
        gui_values = {
            "target_grade": "grade1",
            "dictionary_input_dir": "input",
            "dictionary_output_dir": "output",
            "reference_dictionary_path": "reference.jsonl",
            "model": "gemma-test",
            "base_url": "",
            "dictionary_force": False,
            "include_numbers": True,
            "include_punctuation": False,
        }

        self.assertEqual(build_cli_request(cli_args), build_gui_request("dictionary_build", gui_values))

    def test_hosted_provider_runtime_flows_into_requests(self) -> None:
        parser = build_parser()
        cli_args = parser.parse_args(
            [
                "--build-dictionary",
                "--llm-provider",
                "gemini",
                "--llm-model",
                "gemini-test",
                "--llm-base-url",
                "https://example.test/gemini",
            ]
        )
        gui_values = {
            "target_grade": "grade1",
            "dictionary_input_dir": "build-dictionary/original/grade1",
            "dictionary_output_dir": "build-dictionary/build/grade1",
            "reference_dictionary_path": "build-dictionary/references/reference-dictionary.jsonl",
            "provider": "google",
            "model": "gemini-test",
            "base_url": "https://example.test/gemini",
            "dictionary_force": False,
            "include_numbers": False,
            "include_punctuation": False,
        }

        cli_request = build_cli_request(cli_args)
        gui_request = build_gui_request("dictionary_build", gui_values)

        self.assertEqual(cli_request.provider, "google")  # type: ignore[union-attr]
        self.assertEqual(gui_request.provider, "google")  # type: ignore[union-attr]
        self.assertEqual(cli_request.model, "gemini-test")  # type: ignore[union-attr]
        self.assertEqual(gui_request.base_url, "https://example.test/gemini")  # type: ignore[union-attr]

    def test_class_printouts_cli_and_gui_requests_match(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            rewrite_dictionary = Path(workspace) / "live.jsonl"
            rewrite_dictionary.write_text("{}\n", encoding="utf-8")
            parser = build_parser()
            cli_args = parser.parse_args(
                [
                    "--build-class-printouts",
                    "sources",
                    "--target-grade",
                    "grade1",
                    "--output",
                    "dictionary-output",
                    "--source-output-root",
                    "parsed",
                    "--rewrite-output-root",
                    "rewritten",
                    "--print-output-root",
                    "printed",
                    "--rewrite-dictionary",
                    str(rewrite_dictionary),
                    "--live-lexicon",
                    "live.jsonl",
                    "--ollama-model",
                    "gemma-test",
                    "--pdf-render-dpi",
                    "180",
                    "--skip-pdf-parse",
                    "--force-class-printouts",
                ]
            )
            gui_values = {
                "target_grade": "grade1",
                "class_source_dir": "sources",
                "source_output_root": "parsed",
                "rewrite_output_root": "rewritten",
                "print_output_root": "printed",
                "dictionary_output_dir": "dictionary-output",
                "live_lexicon_path": "live.jsonl",
                "rewrite_dictionary_path": str(rewrite_dictionary),
                "model": "gemma-test",
                "base_url": "",
                "force_class_printouts": True,
                "skip_pdf_parse": True,
                "pdf_render_dpi": "180",
                "print_font_path": "",
                "print_body_font_size": "",
            }

            self.assertEqual(build_cli_request(cli_args), build_gui_request("class_printouts", gui_values))

    def test_archive_rename_cli_and_gui_requests_match_except_adapter_id(self) -> None:
        parser = build_parser()
        cli_args = parser.parse_args(
            [
                "--rename-class-session-archive",
                "archive",
                "--apply-archive-renames",
                "--target-grade",
                "grade1",
                "--ollama-model",
                "gemma-test",
            ]
        )
        gui_values = {
            "target_grade": "grade1",
            "archive_root": "archive",
            "model": "gemma-test",
            "base_url": "",
        }
        cli_request = build_cli_request(cli_args)
        gui_request = build_gui_request("archive_rename_apply", gui_values)

        self.assertIsInstance(cli_request, ArchiveRenameRequest)
        self.assertIsInstance(gui_request, ArchiveRenameRequest)
        self.assertEqual(_without_command_id(cli_request), _without_command_id(gui_request))

    def test_run_request_dispatches_dictionary_build_to_application_handler(self) -> None:
        request = DictionaryBuildRequest(
            command_id=CommandId.DICTIONARY_BUILD,
            target_grade="grade1",
            input_dir=Path("input"),
            output_dir=Path("out"),
            reference_dictionary_path=Path("reference.jsonl"),
            include_punctuation=False,
            include_numbers=False,
            model="gemma-test",
            base_url=None,
            force=False,
            provider="ollama",
            target_checkpoint="15",
        )

        with patch("wordbyword.application.use_cases.build_resumable_dictionary") as build_dictionary:
            build_dictionary.return_value = SimpleNamespace(
                pipeline_result=PipelineResult(
                    documents=0,
                    extracted_pages=0,
                    sentences=0,
                    token_occurrences=0,
                    vocabulary_terms=0,
                    surface_words=0,
                    output_dir=Path("out"),
                ),
                statuses=(),
            )

            result = run_request(request)

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(build_dictionary.called)

    def test_cli_run_immediate_command_delegates_to_application_boundary(self) -> None:
        from wordbyword.cli import app as cli_app

        parser = build_parser()
        args = parser.parse_args(["--show-distribution", "dictionary.jsonl"])

        with patch(
            "wordbyword.application.use_cases.run_request",
            return_value=CommandResult(17, "failed", "missing", error="missing"),
        ) as app_run_request:
            exit_code = cli_app.run_immediate_command(args)

        self.assertEqual(exit_code, 17)
        app_run_request.assert_called_once()
        self.assertIsInstance(app_run_request.call_args.args[0], ShowDistributionRequest)

    def test_gui_run_workflow_delegates_to_application_boundary(self) -> None:
        from wordbyword.gui import workflows

        request = ShowDistributionRequest(
            command_id=CommandId.SHOW_DISTRIBUTION,
            jsonl_path=Path("dictionary.jsonl"),
            model="gemma-test",
        )

        def emit(event: object) -> None:
            return None

        with (
            patch("wordbyword.gui.workflows.build_gui_request", return_value=request) as build_request,
            patch(
                "wordbyword.application.use_cases.run_request",
                return_value=CommandResult(0, "complete", "done"),
            ) as app_run_request,
        ):
            result = workflows.run_workflow("dictionary_build", {"model": "gemma-test"}, emit)

        build_request.assert_called_once_with("dictionary_build", {"model": "gemma-test"})
        app_run_request.assert_called_once()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.detail, "done")

    def test_application_package_exports_run_request(self) -> None:
        import wordbyword.application as application

        request = ShowDistributionRequest(
            command_id=CommandId.SHOW_DISTRIBUTION,
            jsonl_path=Path("dictionary.jsonl"),
            model="gemma-test",
        )

        with patch(
            "wordbyword.application.use_cases.show_distribution",
            return_value=CommandResult(0, "complete", "ok"),
        ) as show_distribution:
            result = application.run_request(request)

        self.assertEqual(result.exit_code, 0)
        show_distribution.assert_called_once_with(request)


def _without_command_id(request: object) -> dict[str, object]:
    return {
        field.name: getattr(request, field.name)
        for field in fields(request)
        if field.name not in {"command_id"}
    }
