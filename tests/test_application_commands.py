from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wordbyword.application.commands import (
    ArchiveRenameRequest,
    ClassPrintoutsRequest,
    CommandId,
    CommandResult,
    DictionaryBuildRequest,
    ShowDistributionRequest,
    command_spec,
    registered_command_ids,
)
from wordbyword.application.use_cases import run_request
from wordbyword.application.workspace import RewriteVocabularySource
from wordbyword.cli import build_parser
from wordbyword.cli.request_builder import build_cli_request, command_id_from_cli_args
from wordbyword.dictionary_build.pipeline import PipelineResult


class ApplicationCommandRegistryTests(unittest.TestCase):
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
        resolved_ids: set[CommandId] = set()

        for argv, expected_label in cases.items():
            with self.subTest(argv=argv):
                args = parser.parse_args([*argv, "--target-grade", "grade1"] if "--show-distribution" not in argv else list(argv))
                command_id = command_id_from_cli_args(args)

                self.assertIsNotNone(command_id)
                self.assertEqual(command_spec(command_id).lock_label, expected_label)
                resolved_ids.add(command_id)

        self.assertEqual(resolved_ids, set(registered_command_ids()))

    def test_application_commands_import_does_not_load_adapters(self) -> None:
        code = (
            "import sys\n"
            "import wordbyword.application.commands\n"
            "unexpected = [\n"
            "    name\n"
            "    for name in (\n"
            "        'wordbyword.cli.app',\n"
            "        'wordbyword.application.workspace',\n"
            "        'wordbyword.classroom_printout_studio.source_parsing.materials',\n"
            "        'wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl',\n"
            "        'fitz',\n"
            "    )\n"
            "    if name in sys.modules\n"
            "]\n"
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
            "if 'wordbyword.cli.app' in sys.modules:\n"
            "    raise SystemExit('wordbyword.cli.app')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cli_adapter_does_not_import_domain_workflows(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        adapter_paths = [
            project_root / "wordbyword" / "cli" / "app.py",
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
    def test_dictionary_cli_options_build_request(self) -> None:
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
        expected = DictionaryBuildRequest(
            command_id=CommandId.DICTIONARY_BUILD,
            target_checkpoint="15",
            target_grade="grade1",
            input_dir=Path("input"),
            output_dir=Path("output"),
            reference_dictionary_path=Path("reference.jsonl"),
            model="gemma-test",
            base_url=None,
            force=False,
            include_numbers=True,
            include_punctuation=False,
        )

        self.assertEqual(build_cli_request(cli_args), expected)

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
        cli_request = build_cli_request(cli_args)

        self.assertEqual(cli_request.provider, "google")  # type: ignore[union-attr]
        self.assertEqual(cli_request.model, "gemini-test")  # type: ignore[union-attr]
        self.assertEqual(cli_request.base_url, "https://example.test/gemini")  # type: ignore[union-attr]

    def test_class_printouts_cli_options_build_request(self) -> None:
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
            expected = ClassPrintoutsRequest(
                command_id=CommandId.CLASS_PRINTOUTS,
                target_grade="grade1",
                source_dir=Path("sources"),
                parse_output_root=Path("parsed"),
                rewrite_output_root=Path("rewritten"),
                print_output_root=Path("printed"),
                dictionary_output_dir=Path("dictionary-output"),
                live_lexicon_path=Path("live.jsonl"),
                rewrite_dictionary_path=rewrite_dictionary,
                vocabulary_source=RewriteVocabularySource(
                    path=rewrite_dictionary,
                    kind="explicit",
                    sha256=hashlib.sha256(b"{}\n").hexdigest(),
                ),
                model="gemma-test",
                base_url=None,
                force_class_printouts=True,
                parse_pdf=False,
                render_dpi=180,
                paddleocr_vl_settings=None,
                font_path=None,
                body_font_size=None,
            )

            self.assertEqual(build_cli_request(cli_args), expected)

    def test_archive_rename_cli_options_build_request(self) -> None:
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
        expected = ArchiveRenameRequest(
            command_id=CommandId.RENAME_CLASS_SESSION_ARCHIVE,
            target_grade="grade1",
            archive_root=Path("archive"),
            model="gemma-test",
            base_url=None,
            apply=True,
        )

        self.assertEqual(build_cli_request(cli_args), expected)

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
