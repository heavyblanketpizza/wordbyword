from __future__ import annotations

import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wordbyword.after_class_reports import AfterClassReportProgress
from wordbyword.cli import (
    DEFAULT_INPUT_DIR,
    DEFAULT_REFERENCE_DICTIONARY_PATH,
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    build_parser,
    add_reference_meanings,
    generate_missing_meanings,
    link_cleaned_examples,
    load_dictionary_candidates,
    main,
    paddleocr_vl_settings_from_args,
    prompt_generated_example_retry,
    resolve_distribution_path,
    show_distribution,
    source_parse_options_from_args,
    validate_args,
)
from wordbyword.dictionary_build.pipeline import PipelineUsageError
from wordbyword.dictionary_build.example_generation import GeneratedExampleAttemptSummary
from wordbyword.dictionary_build.llm import CompletionResult, LLMConfigurationError
from wordbyword.dictionary_build.pipeline import PipelineResult
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import SourceRewriteProgress
from wordbyword.classroom_printout_studio.class_printing.printable import PrintableProgress
from wordbyword.classroom_printout_studio.source_parsing.materials import DEFAULT_SOURCE_MATERIAL_ROOT
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class ParserDefaultsTest(unittest.TestCase):
    def test_dictionary_input_defaults_live_under_build_dictionary(self) -> None:
        args = build_parser().parse_args([])

        self.assertEqual(args.input, DEFAULT_INPUT_DIR)
        self.assertEqual(args.input, Path("build-dictionary/original"))
        self.assertFalse(args.build_dictionary)
        self.assertFalse(args.prep_dictionary)
        self.assertEqual(args.reference_dictionary, DEFAULT_REFERENCE_DICTIONARY_PATH)
        self.assertEqual(
            args.reference_dictionary,
            Path("build-dictionary/references/reference-dictionary.jsonl"),
        )

    def test_build_dictionary_accepts_normal_dictionary_modifiers(self) -> None:
        args = build_parser().parse_args(
            [
                "--build-dictionary",
                "--force",
                "--input",
                "custom-input",
                "--output",
                "custom-output",
                "--reference-dictionary",
                "references.jsonl",
                "--ollama-model",
                "gemma4:31b",
                "--ollama-base-url",
                "http://localhost:11434",
                "--include-punctuation",
                "--include-numbers",
            ]
        )

        self.assertTrue(args.build_dictionary)
        self.assertTrue(args.force)
        self.assertEqual(args.input, Path("custom-input"))
        self.assertEqual(args.output, Path("custom-output"))
        self.assertEqual(args.reference_dictionary, Path("references.jsonl"))
        self.assertEqual(args.ollama_model, "gemma4:31b")
        self.assertEqual(args.ollama_base_url, "http://localhost:11434")
        self.assertTrue(args.include_punctuation)
        self.assertTrue(args.include_numbers)

    def test_prep_dictionary_accepts_normal_dictionary_modifiers(self) -> None:
        args = build_parser().parse_args(
            [
                "--prep-dictionary",
                "--force",
                "--input",
                "custom-input",
                "--output",
                "custom-output",
                "--ollama-model",
                "gemma4:31b",
                "--ollama-base-url",
                "http://localhost:11434",
                "--include-punctuation",
                "--include-numbers",
            ]
        )

        self.assertTrue(args.prep_dictionary)
        self.assertTrue(args.force)
        self.assertEqual(args.input, Path("custom-input"))
        self.assertEqual(args.output, Path("custom-output"))
        self.assertEqual(args.ollama_model, "gemma4:31b")
        self.assertEqual(args.ollama_base_url, "http://localhost:11434")
        self.assertTrue(args.include_punctuation)
        self.assertTrue(args.include_numbers)

    def test_no_command_is_rejected(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        stderr = io.StringIO()

        with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
            validate_args(parser, args)

        self.assertEqual(error.exception.code, 2)
        self.assertIn("choose an explicit command", stderr.getvalue())

    def test_build_dictionary_rejects_incompatible_commands(self) -> None:
        incompatible_args = [
            ["--clean-example-sentences"],
            ["--clean-dictionary-candidates"],
            ["--link-cleaned-examples"],
            ["--generate-missing-examples"],
            ["--retry-generated-examples"],
            ["--add-reference-meanings"],
            ["--generate-missing-meanings"],
            ["--show-distribution"],
            ["--build-class-printouts"],
            ["--build-usage-signals"],
        ]

        for extra_args in incompatible_args:
            with self.subTest(extra_args=extra_args):
                parser = build_parser()
                args = parser.parse_args(["--build-dictionary", *extra_args])
                stderr = io.StringIO()
                with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
                    validate_args(parser, args)

                self.assertEqual(error.exception.code, 2)
                self.assertIn("--build-dictionary cannot be combined", stderr.getvalue())

    def test_prep_dictionary_rejects_incompatible_commands(self) -> None:
        incompatible_args = [
            ["--clean-example-sentences"],
            ["--clean-dictionary-candidates"],
            ["--link-cleaned-examples"],
            ["--generate-missing-examples"],
            ["--retry-generated-examples"],
            ["--add-reference-meanings"],
            ["--generate-missing-meanings"],
            ["--show-distribution"],
            ["--build-class-printouts"],
            ["--build-usage-signals"],
        ]

        for extra_args in incompatible_args:
            with self.subTest(extra_args=extra_args):
                parser = build_parser()
                args = parser.parse_args(["--prep-dictionary", *extra_args])
                stderr = io.StringIO()
                with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
                    validate_args(parser, args)

                self.assertEqual(error.exception.code, 2)
                self.assertIn("--prep-dictionary cannot be combined", stderr.getvalue())

    def test_force_without_dictionary_build_command_is_rejected(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--force"])
        stderr = io.StringIO()

        with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
            validate_args(parser, args)

        self.assertEqual(error.exception.code, 2)
        self.assertIn("--force can only be used with --prep-dictionary or --build-dictionary", stderr.getvalue())

    def test_class_printout_commands_have_zero_config_roots(self) -> None:
        parser = build_parser()

        parse_args = parser.parse_args(["--parse-source-materials"])
        print_args = parser.parse_args(["--print-class-materials"])
        workflow_args = parser.parse_args(["--build-class-printouts"])

        self.assertEqual(parse_args.parse_source_materials, DEFAULT_SOURCE_MATERIAL_ROOT)
        self.assertEqual(print_args.print_class_materials, DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT)
        self.assertEqual(workflow_args.build_class_printouts, DEFAULT_SOURCE_MATERIAL_ROOT)

    def test_removed_pdf_parser_option_is_rejected(self) -> None:
        parser = build_parser()

        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--pdf-parser", "heuristic"])

    def test_paddleocr_vl_settings_are_built_for_source_material_parser(self) -> None:
        parser = build_parser()

        paddle_args = parser.parse_args(
            [
                "--paddleocr-vl-device",
                "cpu",
                "--paddleocr-vl-engine",
                "paddle",
                "--paddleocr-vl-doc-orientation",
                "--paddleocr-vl-no-layout-detection",
                "--paddleocr-vl-no-ocr-for-image-block",
                "--paddleocr-vl-max-new-tokens",
                "2048",
                "--paddleocr-vl-sidecar-env",
                "/tmp/paddle-env",
                "--paddleocr-vl-sidecar-python",
                "/tmp/paddle-env/bin/python",
                "--paddleocr-vl-sidecar-timeout-seconds",
                "120",
            ]
        )
        with patch("wordbyword.cli.app.validate_paddleocr_vl_sidecar") as validate_sidecar:
            settings = paddleocr_vl_settings_from_args(paddle_args)

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.device, "cpu")
        self.assertEqual(settings.engine, "paddle")
        self.assertTrue(settings.use_doc_orientation_classify)
        self.assertFalse(settings.use_layout_detection)
        self.assertFalse(settings.use_ocr_for_image_block)
        self.assertEqual(settings.max_new_tokens, 2048)
        self.assertEqual(settings.sidecar_env, "/tmp/paddle-env")
        self.assertEqual(settings.sidecar_python, "/tmp/paddle-env/bin/python")
        self.assertEqual(settings.sidecar_timeout_seconds, 120)
        validate_sidecar.assert_called_once_with(
            sidecar_env="/tmp/paddle-env",
            sidecar_python="/tmp/paddle-env/bin/python",
        )

    def test_paddleocr_vl_settings_auto_discovers_local_sidecar(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--parse-source-materials"])

        with (
            patch("wordbyword.cli.app.discover_paddleocr_vl_sidecar_env", return_value=Path(".venv-paddleocr-vl")),
            patch("wordbyword.cli.app.validate_paddleocr_vl_sidecar") as validate_sidecar,
        ):
            settings = source_parse_options_from_args(args).paddleocr_vl_settings

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.sidecar_env, ".venv-paddleocr-vl")
        self.assertIsNone(settings.sidecar_python)
        validate_sidecar.assert_called_once_with(sidecar_env=".venv-paddleocr-vl", sidecar_python=None)

    def test_paddleocr_vl_settings_respects_explicit_sidecar_over_auto_discovery(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--parse-source-materials",
                "--paddleocr-vl-sidecar-env",
                "/tmp/explicit-paddle-env",
            ]
        )

        with (
            patch("wordbyword.cli.app.discover_paddleocr_vl_sidecar_env", return_value=Path(".venv-paddleocr-vl")),
            patch("wordbyword.cli.app.validate_paddleocr_vl_sidecar") as validate_sidecar,
        ):
            settings = source_parse_options_from_args(args).paddleocr_vl_settings

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.sidecar_env, "/tmp/explicit-paddle-env")
        validate_sidecar.assert_called_once_with(sidecar_env="/tmp/explicit-paddle-env", sidecar_python=None)

    def test_paddleocr_vl_settings_uses_env_sidecar_before_local_discovery(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--parse-source-materials"])

        with (
            patch.dict("os.environ", {"WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV": "/tmp/env-paddle"}, clear=True),
            patch("wordbyword.cli.app.discover_paddleocr_vl_sidecar_env") as discover_sidecar,
            patch("wordbyword.cli.app.validate_paddleocr_vl_sidecar") as validate_sidecar,
        ):
            settings = source_parse_options_from_args(args).paddleocr_vl_settings

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.sidecar_env, "/tmp/env-paddle")
        discover_sidecar.assert_not_called()
        validate_sidecar.assert_called_once_with(sidecar_env="/tmp/env-paddle", sidecar_python=None)

    def test_paddleocr_vl_settings_fail_fast_when_no_runtime_is_available(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--parse-source-materials"])

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("wordbyword.cli.app.discover_paddleocr_vl_sidecar_env", return_value=None),
            patch("wordbyword.cli.app.current_python_has_paddleocr", return_value=False),
        ):
            with self.assertRaises(PipelineUsageError):
                source_parse_options_from_args(args)

    def test_skip_pdf_parse_does_not_require_paddleocr_vl_runtime(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--parse-source-materials", "--skip-pdf-parse"])

        with (
            patch("wordbyword.cli.app.discover_paddleocr_vl_sidecar_env") as discover_sidecar,
            patch("wordbyword.cli.app.current_python_has_paddleocr") as current_has_paddleocr,
        ):
            options = source_parse_options_from_args(args)

        self.assertIsNone(options.paddleocr_vl_settings)
        discover_sidecar.assert_not_called()
        current_has_paddleocr.assert_not_called()

    def test_main_reports_source_material_interrupt_with_130_exit(self) -> None:
        stderr = io.StringIO()
        with (
            patch("sys.stderr", stderr),
            patch("sys.stdout", io.StringIO()),
            patch("wordbyword.cli.app.parse_source_materials", side_effect=KeyboardInterrupt),
        ):
            exit_code = main(["--parse-source-materials", "classroom-printout-studio/class-source-materials"])

        self.assertEqual(exit_code, 130)
        self.assertIn("interrupted", stderr.getvalue())

    def test_build_class_printouts_runs_only_the_bundles_parsed_in_this_run(self) -> None:
        parsed_bundles = [
            SimpleNamespace(source_id="lesson-a", output_dir=Path("/tmp/parsed/lesson-a")),
            SimpleNamespace(source_id="lesson-b", output_dir=Path("/tmp/parsed/lesson-b")),
        ]
        parsed_result = SimpleNamespace(
            source_dir=DEFAULT_SOURCE_MATERIAL_ROOT,
            output_root=Path("classroom-printout-studio/class-source-materials/parsed"),
            bundles=parsed_bundles,
        )
        rewrite_outputs = [
            SimpleNamespace(
                source_id="lesson-a",
                output_dir=Path("/tmp/rewritten/lesson-a"),
                failed_page_count=0,
                rewritten_page_count=2,
                selected_vocabulary=[{"canonical_key": "물"}],
            ),
            SimpleNamespace(
                source_id="lesson-b",
                output_dir=Path("/tmp/rewritten/lesson-b"),
                failed_page_count=0,
                rewritten_page_count=3,
                selected_vocabulary=[{"canonical_key": "빛"}],
            ),
        ]
        print_outputs = [
            SimpleNamespace(source_id="lesson-a", output_dir=Path("/tmp/print/lesson-a"), accepted=True),
            SimpleNamespace(source_id="lesson-b", output_dir=Path("/tmp/print/lesson-b"), accepted=True),
        ]
        clients = [object(), object()]

        with (
            patch("sys.stdout", io.StringIO()),
            patch("wordbyword.cli.app.paddleocr_vl_settings_from_args", return_value=PaddleOCRVLSettings(sidecar_env=".venv-paddleocr-vl")),
            patch("wordbyword.cli.app.parse_source_materials", return_value=parsed_result) as parse_materials,
            patch(
                "wordbyword.cli.app.rewrite_vocabulary_guided_parsed_source_material",
                side_effect=rewrite_outputs,
            ) as rewrite_material,
            patch("wordbyword.cli.app.print_class_material", side_effect=print_outputs) as print_material,
            patch("wordbyword.cli.app.ollama_client", side_effect=clients),
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            exit_code = main(
                [
                    "--build-class-printouts",
                    "--rewrite-dictionary",
                    "build-dictionary/15_final_dictionary.jsonl",
                    "--rewrite-output-root",
                    "/tmp/rewritten",
                    "--print-output-root",
                    "/tmp/print",
                    "--print-body-font-size",
                    "13",
                    "--print-font-path",
                    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
                ]
            )

        self.assertEqual(exit_code, 0)
        parse_materials.assert_called_once()
        self.assertEqual(parse_materials.call_args.args[0], DEFAULT_SOURCE_MATERIAL_ROOT)
        self.assertEqual(parse_materials.call_args.kwargs["paddleocr_vl_settings"].sidecar_env, ".venv-paddleocr-vl")
        self.assertEqual(rewrite_material.call_args_list[0].args[0], Path("/tmp/parsed/lesson-a"))
        self.assertEqual(rewrite_material.call_args_list[1].args[0], Path("/tmp/parsed/lesson-b"))
        self.assertEqual(
            [rewrite_call.kwargs["dictionary_path"] for rewrite_call in rewrite_material.call_args_list],
            [Path("build-dictionary/15_final_dictionary.jsonl"), Path("build-dictionary/15_final_dictionary.jsonl")],
        )
        self.assertEqual(
            [rewrite_call.kwargs["output_root"] for rewrite_call in rewrite_material.call_args_list],
            [Path("/tmp/rewritten"), Path("/tmp/rewritten")],
        )
        self.assertEqual(print_material.call_args_list[0].args[0], Path("/tmp/rewritten/lesson-a"))
        self.assertEqual(print_material.call_args_list[1].args[0], Path("/tmp/rewritten/lesson-b"))
        self.assertEqual(
            [print_call.kwargs["output_root"] for print_call in print_material.call_args_list],
            [Path("/tmp/print"), Path("/tmp/print")],
        )
        self.assertEqual(
            [print_call.kwargs["body_font_size"] for print_call in print_material.call_args_list],
            [13, 13],
        )

    def test_build_class_printouts_returns_nonzero_when_a_rewrite_fails(self) -> None:
        parsed_result = SimpleNamespace(
            source_dir=DEFAULT_SOURCE_MATERIAL_ROOT,
            output_root=Path("classroom-printout-studio/class-source-materials/parsed"),
            bundles=[SimpleNamespace(source_id="lesson-a", output_dir=Path("/tmp/parsed/lesson-a"))],
        )
        rewrite_output = SimpleNamespace(
            source_id="lesson-a",
            output_dir=Path("/tmp/rewritten/lesson-a"),
            failed_page_count=1,
            rewritten_page_count=0,
            selected_vocabulary=[],
        )

        with (
            patch("sys.stdout", io.StringIO()),
            patch("wordbyword.cli.app.paddleocr_vl_settings_from_args", return_value=PaddleOCRVLSettings(sidecar_env=".venv-paddleocr-vl")),
            patch("wordbyword.cli.app.parse_source_materials", return_value=parsed_result),
            patch(
                "wordbyword.cli.app.rewrite_vocabulary_guided_parsed_source_material",
                return_value=rewrite_output,
            ),
            patch("wordbyword.cli.app.print_class_material") as print_material,
            patch("wordbyword.cli.app.ollama_client", return_value=object()),
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            exit_code = main(["--build-class-printouts"])

        self.assertEqual(exit_code, 1)
        print_material.assert_not_called()

    def test_interactive_source_rewrite_output_uses_styled_terminal_ui(self) -> None:
        stdout = TtyStringIO()

        def rewrite_side_effect(*args: object, **kwargs: object) -> SimpleNamespace:
            progress = kwargs["progress"]
            assert callable(progress)
            progress(
                SourceRewriteProgress(
                    phase="rewrite",
                    source_id="lesson",
                    detail="rewriting whole document with selected vocabulary",
                    rewrite_mode="vocabulary_guided",
                )
            )
            return SimpleNamespace(
                source_id="lesson",
                output_dir=Path("/tmp/rewritten/lesson"),
                failed_page_count=0,
                rewritten_page_count=2,
                selected_vocabulary=[{"canonical_key": "물"}],
            )

        with (
            patch.dict("os.environ", {"TERM": "xterm-256color"}),
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.rewrite_vocabulary_guided_parsed_source_material", side_effect=rewrite_side_effect),
            patch("wordbyword.cli.app.ollama_client", return_value=object()),
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            exit_code = main(
                [
                    "--rewrite-vocabulary-guided-source-material",
                    "/tmp/parsed/lesson",
                    "--rewrite-dictionary",
                    "build-dictionary/15_final_dictionary.jsonl",
                ]
            )

        self.assertEqual(exit_code, 0)
        rendered = stdout.getvalue()
        self.assertIn("SOURCE REWRITE // VOCABULARY GUIDED", rendered)
        self.assertIn("REWRITE", rendered)
        self.assertNotIn("rewrite source [", rendered)

    def test_interactive_print_class_material_output_uses_styled_terminal_ui(self) -> None:
        stdout = TtyStringIO()

        def print_side_effect(*args: object, **kwargs: object) -> SimpleNamespace:
            progress = kwargs["progress"]
            assert callable(progress)
            progress(
                PrintableProgress(
                    phase="plan",
                    source_id="lesson",
                    detail="planning printable reading material",
                )
            )
            return SimpleNamespace(
                source_id="lesson",
                output_dir=Path("/tmp/print/lesson"),
                accepted=True,
                failure_reason="",
                attempt_rows=[{"selected_asset_ids": ["fig1"], "body_font_size_pt": 13}],
                failure_rows=[],
            )

        with (
            patch.dict("os.environ", {"TERM": "xterm-256color"}),
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.print_class_material", side_effect=print_side_effect),
            patch("wordbyword.cli.app.ollama_client", return_value=object()),
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            exit_code = main(["--print-class-material", "/tmp/rewritten/lesson"])

        self.assertEqual(exit_code, 0)
        rendered = stdout.getvalue()
        self.assertIn("PRINTABLE CLASS // DOCX MATERIAL", rendered)
        self.assertIn("PLAN", rendered)
        self.assertIn("material.docx", rendered)
        self.assertNotIn("print class [", rendered)

    def test_interactive_print_class_materials_output_renders_styled_summary(self) -> None:
        stdout = TtyStringIO()
        outputs = SimpleNamespace(
            rewritten_root=Path("/tmp/rewritten"),
            output_root=Path("/tmp/print"),
            source_outputs=[
                SimpleNamespace(
                    source_id="lesson",
                    output_dir=Path("/tmp/print/lesson"),
                    accepted=True,
                    failure_reason="",
                    attempt_rows=[{"selected_asset_ids": ["fig1"], "body_font_size_pt": 13}],
                    failure_rows=[],
                )
            ],
            skipped_sources=[],
        )

        with (
            patch.dict("os.environ", {"TERM": "xterm-256color"}),
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.print_class_materials", return_value=outputs),
            patch("wordbyword.cli.app.ollama_client", return_value=object()),
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            exit_code = main(["--print-class-materials", "/tmp/rewritten"])

        self.assertEqual(exit_code, 0)
        rendered = stdout.getvalue()
        self.assertIn("PRINTABLE CLASS // DOCX MATERIALS", rendered)
        self.assertIn("written", rendered)
        self.assertNotIn("print class [", rendered)

    def test_interactive_build_class_printouts_print_phase_uses_styled_terminal_ui(self) -> None:
        stdout = TtyStringIO()
        parsed_result = SimpleNamespace(
            source_dir=DEFAULT_SOURCE_MATERIAL_ROOT,
            output_root=Path("/tmp/parsed"),
            bundles=[SimpleNamespace(source_id="lesson", output_dir=Path("/tmp/parsed/lesson"))],
        )
        rewrite_output = SimpleNamespace(
            source_id="lesson",
            output_dir=Path("/tmp/rewritten/lesson"),
            failed_page_count=0,
            rewritten_page_count=2,
            selected_vocabulary=[{"canonical_key": "물"}],
        )

        def print_side_effect(*args: object, **kwargs: object) -> SimpleNamespace:
            progress = kwargs["progress"]
            assert callable(progress)
            progress(
                PrintableProgress(
                    phase="plan",
                    source_id="lesson",
                    detail="planning printable reading material",
                    source_index=1,
                    total_sources=1,
                )
            )
            return SimpleNamespace(
                source_id="lesson",
                output_dir=Path("/tmp/print/lesson"),
                accepted=True,
                failure_reason="",
                attempt_rows=[{"selected_asset_ids": ["fig1"], "body_font_size_pt": 13}],
                failure_rows=[],
            )

        with (
            patch.dict("os.environ", {"TERM": "xterm-256color"}),
            patch("sys.stdout", stdout),
            patch("wordbyword.cli.app.paddleocr_vl_settings_from_args", return_value=PaddleOCRVLSettings(sidecar_env=".venv-paddleocr-vl")),
            patch("wordbyword.cli.app.parse_source_materials", return_value=parsed_result),
            patch("wordbyword.cli.app.rewrite_vocabulary_guided_parsed_source_material", return_value=rewrite_output),
            patch("wordbyword.cli.app.print_class_material", side_effect=print_side_effect),
            patch("wordbyword.cli.app.ollama_client", side_effect=[object(), object()]),
            patch("wordbyword.cli.app.warn_if_model_unload_fails"),
        ):
            exit_code = main(["--build-class-printouts"])

        self.assertEqual(exit_code, 0)
        rendered = stdout.getvalue()
        self.assertIn("PRINTABLE CLASS // WORKFLOW COMPLETE", rendered)
        self.assertIn("PLAN", rendered)
        self.assertNotIn("print class [", rendered)

    def test_interactive_after_class_report_output_uses_styled_terminal_ui(self) -> None:
        stdout = TtyStringIO()
        source = Path("/tmp/2026-05-10_grade1-reading-apples.docx")
        outputs = SimpleNamespace(
            source_id=source.stem,
            output_dir=Path("/tmp/reports") / source.stem,
            heuristic_analysis_dir=Path("/tmp/reports") / source.stem / "heuristic-analysis",
            accepted=True,
            failure_reason="",
            selected_vocabulary_count=15,
            docx_path=Path("/tmp/reports") / source.stem / "after_class_report.docx",
            attempt_rows=[],
            failure_rows=[],
        )

        def generate_side_effect(*args: object, **kwargs: object) -> SimpleNamespace:
            progress = kwargs["progress"]
            assert callable(progress)
            progress(
                AfterClassReportProgress(
                    phase="plan",
                    source_id=source.stem,
                    detail="generating adult-facing Korean report plan with Ollama",
                    output_dir=outputs.output_dir,
                    heuristic_analysis_dir=outputs.heuristic_analysis_dir,
                )
            )
            return outputs

        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / source.name
            source.write_text("placeholder", encoding="utf-8")
            outputs.source_id = source.stem
            outputs.output_dir = Path(workspace) / "reports" / source.stem
            outputs.heuristic_analysis_dir = outputs.output_dir / "heuristic-analysis"
            outputs.docx_path = outputs.output_dir / "after_class_report.docx"
            with (
                patch.dict("os.environ", {"TERM": "xterm-256color"}),
                patch("sys.stdout", stdout),
                patch("wordbyword.cli.app.generate_after_class_reading_report", side_effect=generate_side_effect),
                patch("wordbyword.cli.app.ollama_client", return_value=object()),
                patch("wordbyword.cli.app.warn_if_model_unload_fails"),
            ):
                exit_code = main(["--generate-after-class-reading-report", str(source)])

        self.assertEqual(exit_code, 0)
        rendered = stdout.getvalue()
        self.assertIn("AFTER-CLASS REPORT // READING REPORT", rendered)
        self.assertIn("PLAN", rendered)
        self.assertIn("after_class_report.docx", rendered)
        self.assertNotIn("after-class report [", rendered)


class BuildDictionaryCliTest(unittest.TestCase):
    def test_main_with_build_dictionary_uses_resumable_builder(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            output_dir = root / "output"
            output_dir.mkdir()
            input_dir = root / "input"
            reference_path = root / "reference.jsonl"

            with (
                patch("sys.stdout", io.StringIO()),
                patch(
                    "wordbyword.cli.app.build_resumable_dictionary",
                    return_value=SimpleNamespace(pipeline_result=pipeline_result(output_dir)),
                ) as build_dictionary,
            ):
                exit_code = main(
                    [
                        "--build-dictionary",
                        "--force",
                        "--input",
                        str(input_dir),
                        "--output",
                        str(output_dir),
                        "--reference-dictionary",
                        str(reference_path),
                        "--ollama-model",
                        "gemma4:31b",
                        "--ollama-base-url",
                        "http://localhost:11434",
                        "--include-punctuation",
                        "--include-numbers",
                    ]
                )

            self.assertEqual(exit_code, 0)
            build_dictionary.assert_called_once()
            kwargs = build_dictionary.call_args.kwargs
            self.assertEqual(kwargs["input_dir"], input_dir)
            self.assertEqual(kwargs["output_dir"], output_dir)
            self.assertEqual(kwargs["reference_dictionary_path"], reference_path)
            self.assertTrue(kwargs["include_punctuation"])
            self.assertTrue(kwargs["include_numbers"])
            self.assertEqual(kwargs["model"], "gemma4:31b")
            self.assertEqual(kwargs["base_url"], "http://localhost:11434")
            self.assertTrue(kwargs["force"])
            self.assertEqual(kwargs["target_checkpoint"], "15")
            self.assertIsNotNone(kwargs["checkpoint_status"])

    def test_main_with_prep_dictionary_uses_resumable_builder_to_stage_nine(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            output_dir = root / "output"
            output_dir.mkdir()
            input_dir = root / "input"

            with (
                patch("sys.stdout", io.StringIO()),
                patch(
                    "wordbyword.cli.app.build_resumable_dictionary",
                    return_value=SimpleNamespace(pipeline_result=pipeline_result(output_dir)),
                ) as prep_dictionary,
            ):
                exit_code = main(
                    [
                        "--prep-dictionary",
                        "--force",
                        "--input",
                        str(input_dir),
                        "--output",
                        str(output_dir),
                        "--ollama-model",
                        "gemma4:31b",
                        "--ollama-base-url",
                        "http://localhost:11434",
                        "--include-punctuation",
                        "--include-numbers",
                    ]
                )

            self.assertEqual(exit_code, 0)
            prep_dictionary.assert_called_once()
            kwargs = prep_dictionary.call_args.kwargs
            self.assertEqual(kwargs["input_dir"], input_dir)
            self.assertEqual(kwargs["output_dir"], output_dir)
            self.assertTrue(kwargs["include_punctuation"])
            self.assertTrue(kwargs["include_numbers"])
            self.assertEqual(kwargs["model"], "gemma4:31b")
            self.assertEqual(kwargs["base_url"], "http://localhost:11434")
            self.assertTrue(kwargs["force"])
            self.assertEqual(kwargs["target_checkpoint"], "09")

    def test_build_dictionary_reports_resumable_builder_failure(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", stderr), patch(
            "wordbyword.cli.app.build_resumable_dictionary",
            side_effect=ValueError("bad saved artifact"),
        ):
            exit_code = main(["--build-dictionary"])

        self.assertEqual(exit_code, 1)
        self.assertIn("bad saved artifact", stderr.getvalue())

    def test_build_dictionary_stops_on_unresolved_generated_example_failures(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", stderr), patch(
            "wordbyword.cli.app.build_resumable_dictionary",
            side_effect=wordbyword_cli_unresolved_generated_example_failures(),
        ):
            exit_code = main(["--build-dictionary"])

        self.assertEqual(exit_code, 1)
        self.assertIn("1 unresolved generated-example failure", stderr.getvalue())
        self.assertIn("13_generated_example_failures.jsonl", stderr.getvalue())


def wordbyword_cli_unresolved_generated_example_failures():
    from wordbyword.dictionary_build.resumable import UnresolvedGeneratedExampleFailures

    return UnresolvedGeneratedExampleFailures(
        1,
        Path("build-dictionary/13_generated_example_failures.jsonl"),
    )


class ShowDistributionCliTest(unittest.TestCase):
    def test_resolve_distribution_path_uses_final_default_when_no_dictionary_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace) / "build-dictionary"
            output_dir.mkdir()

            path = resolve_distribution_path("", output_dir)

            self.assertEqual(path, output_dir / "15_final_dictionary.jsonl")

    def test_resolve_distribution_path_prefers_final_dictionary_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace) / "build-dictionary"
            output_dir.mkdir()
            (output_dir / "08_dictionary_candidates.jsonl").write_text("{}\n", encoding="utf-8")
            (output_dir / "15_final_dictionary.jsonl").write_text("{}\n", encoding="utf-8")

            path = resolve_distribution_path("", output_dir)

            self.assertEqual(path, output_dir / "15_final_dictionary.jsonl")

    def test_resolve_distribution_path_falls_back_to_latest_available_dictionary_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace) / "build-dictionary"
            output_dir.mkdir()
            (output_dir / "08_dictionary_candidates.jsonl").write_text("{}\n", encoding="utf-8")

            path = resolve_distribution_path("", output_dir)

            self.assertEqual(path, output_dir / "08_dictionary_candidates.jsonl")

    def test_resolve_distribution_path_uses_supplied_value(self) -> None:
        path = resolve_distribution_path("/tmp/relics.jsonl", Path("build-dictionary"))
        self.assertEqual(path, Path("/tmp/relics.jsonl"))

    def test_load_dictionary_candidates_reads_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "11_cleaned_dictionary_candidates.jsonl"
            rows = [
                {"canonical_key": "가다", "learning_signal_score": -0.08, "count": 12},
                {"canonical_key": "오다", "learning_signal_score": 0.21, "count": 88},
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

    def test_show_distribution_prints_final_dictionary_panel(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "15_final_dictionary.jsonl"
            rows = [
                {"learning_signal_score": -0.38, "count": 3},
                {"learning_signal_score": -0.04, "count": 18},
                {"learning_signal_score": 0.41, "count": 220},
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
            self.assertIn("MEANING FORGE // EMPTY SLOTS FILLED", output)
            self.assertIn("3 headwords", output)
            self.assertIn("replay // no llm calls", output)
            self.assertIn("learning_signal_score distribution", output)
            self.assertIn("occurrence count distribution", output)
            self.assertEqual(stderr.getvalue(), "")

    def test_show_distribution_preserves_explicit_stage_eleven_panel(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "11_cleaned_dictionary_candidates.jsonl"
            rows = [
                {"learning_signal_score": -0.38, "count": 3},
                {"learning_signal_score": -0.04, "count": 18},
                {"learning_signal_score": 0.41, "count": 220},
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
            self.assertIn("learning_signal_score distribution", output)
            self.assertIn("occurrence count distribution", output)
            self.assertEqual(stderr.getvalue(), "")

    def test_show_distribution_uses_matching_panel_for_known_intermediate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "14_vocabulary_with_meanings.jsonl"
            jsonl_path.write_text(
                json.dumps({"learning_signal_score": 0.0, "count": 7}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = show_distribution(jsonl_path=jsonl_path, model="gemma4:31b")

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("MEANING RELAY // REFERENCE MERGED", output)
            self.assertIn("learning_signal_score distribution", output)
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
            self.assertIn("15_final_dictionary.jsonl", stderr.getvalue())
            self.assertIn("--generate-missing-meanings", stderr.getvalue())
            self.assertIn("--show-distribution path/to/11_cleaned_dictionary_candidates.jsonl", stderr.getvalue())

    def test_main_with_show_distribution_uses_final_dictionary_default(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace) / "build-dictionary"
            output_dir.mkdir()
            jsonl_path = output_dir / "15_final_dictionary.jsonl"
            jsonl_path.write_text(
                json.dumps({"learning_signal_score": 0.0, "count": 7}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.app.run_pipeline") as mock_pipeline:
                exit_code = main(["--output", str(output_dir), "--show-distribution"])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("15_final_dictionary.jsonl", output)
            self.assertIn("MEANING FORGE // EMPTY SLOTS FILLED", output)
            self.assertIn("learning_signal_score distribution", output)

    def test_main_with_show_distribution_uses_available_dictionary_artifact_without_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace) / "build-dictionary"
            output_dir.mkdir()
            jsonl_path = output_dir / "08_dictionary_candidates.jsonl"
            jsonl_path.write_text(
                json.dumps({"learning_signal_score": 0.0, "count": 7}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.app.run_pipeline") as mock_pipeline:
                exit_code = main(["--output", str(output_dir), "--show-distribution"])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("08_dictionary_candidates.jsonl", output)
            self.assertIn("DICTIONARY RELIC // CANDIDATES LOCKED", output)
            self.assertIn("learning_signal_score distribution", output)

    def test_main_with_show_distribution_skips_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            jsonl_path = Path(workspace) / "relics.jsonl"
            jsonl_path.write_text(
                json.dumps({"learning_signal_score": 0.0, "count": 7}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.app.run_pipeline") as mock_pipeline:
                exit_code = main(["--show-distribution", str(jsonl_path)])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertIn("RELIC DROP //", stdout.getvalue())
            self.assertIn("MEANING FORGE // EMPTY SLOTS FILLED", stdout.getvalue())

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
            with patch("sys.stdout", stdout), patch("wordbyword.cli.app.run_pipeline") as mock_pipeline:
                exit_code = main(["--link-cleaned-examples", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertIn("12_dictionary_candidates_with_cleaned_examples.jsonl", stdout.getvalue())

    def test_add_reference_meanings_builds_stage_fourteen_without_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            output_dir = root / "output"
            output_dir.mkdir()
            reference_path = root / "reference-dictionary.jsonl"
            (output_dir / "13_vocabulary_with_generated_examples.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "canonical_key": "학교",
                                "display": "학교",
                                "roi_score": None,
                                "quality_score_v2": 0.35,
                                "example": "학교에 갑니다.",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "canonical_key": "없는말",
                                "display": "없는말",
                                "example": "없는말 예문",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "13_generated_example_attempts.jsonl").write_text("", encoding="utf-8")
            (output_dir / "13_generated_example_failures.jsonl").write_text("", encoding="utf-8")
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")
            reference_path.write_text(
                json.dumps(
                    {
                        "headword": "학교",
                        "canonical_key": "학교",
                        "meaning": "학생들이 배우는 곳.",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = add_reference_meanings(
                    output_dir=output_dir,
                    reference_dictionary_path=reference_path,
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue((output_dir / "14_vocabulary_with_meanings.jsonl").exists())
            self.assertFalse((output_dir / "13_generated_example_attempts.jsonl").exists())
            self.assertFalse((output_dir / "13_generated_example_failures.jsonl").exists())
            rows = [
                json.loads(line)
                for line in (output_dir / "14_vocabulary_with_meanings.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(rows[0]["meaning"], "학생들이 배우는 곳.")
            self.assertEqual(rows[1]["meaning"], "")
            self.assertTrue(all("roi_score" not in row for row in rows))
            self.assertTrue(all("quality_score_v2" not in row for row in rows))
            self.assertEqual(rows[0]["learning_signal_score"], 0.35)
            self.assertTrue(all(row["curation_status"] == "confirmed" for row in rows))

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("14_vocabulary_with_meanings.jsonl", summary["outputs"])
            self.assertNotIn("13_generated_example_attempts.jsonl", summary["outputs"])
            self.assertNotIn("13_generated_example_failures.jsonl", summary["outputs"])
            self.assertEqual(summary["vocabulary_meaning_rows"], 2)
            self.assertEqual(summary["matched_reference_vocabulary_meanings"], 1)
            self.assertEqual(summary["missing_vocabulary_meanings"], 1)
            self.assertIn("14", {stage["stage"] for stage in summary["pipeline_stages"]})
            self.assertIn("14_vocabulary_with_meanings.jsonl", stdout.getvalue())

    def test_main_with_add_reference_meanings_skips_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            output_dir = root / "output"
            output_dir.mkdir()
            reference_path = root / "reference-dictionary.jsonl"
            (output_dir / "13_vocabulary_with_generated_examples.jsonl").write_text(
                json.dumps({"canonical_key": "물", "display": "물"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            reference_path.write_text(
                json.dumps({"canonical_key": "물", "meaning": "마시는 액체."}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.app.run_pipeline") as mock_pipeline:
                exit_code = main(
                    [
                        "--add-reference-meanings",
                        "--output",
                        str(output_dir),
                        "--reference-dictionary",
                        str(reference_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertIn("14_vocabulary_with_meanings.jsonl", stdout.getvalue())

    def test_generate_missing_meanings_builds_stage_fifteen_without_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)
            (output_dir / "14_vocabulary_with_meanings.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "canonical_key": "학교",
                                "display": "학교",
                                "example": "학교에 갑니다.",
                                "curation_status": "confirmed",
                                "roi_score": None,
                                "quality_score_v2": 0.35,
                                "meaning": "학생들이 배우는 곳.",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "canonical_key": "없는말",
                                "display": "없는말",
                                "example": "없는말 예문",
                                "curation_status": "confirmed",
                                "meaning": "",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                exit_code = generate_missing_meanings(
                    output_dir=output_dir,
                    model="gemma4:31b",
                    generated_meaning_client=GeneratedMeaningClient(),
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue((output_dir / "15_final_dictionary.jsonl").exists())
            rows = [
                json.loads(line)
                for line in (output_dir / "15_final_dictionary.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(rows[0]["meaning"], "학생들이 배우는 곳.")
            self.assertTrue(all("roi_score" not in row for row in rows))
            self.assertTrue(all("quality_score_v2" not in row for row in rows))
            self.assertEqual(rows[0]["learning_signal_score"], 0.35)
            self.assertFalse(rows[0]["meaning_generated_with_ollama"])
            self.assertEqual(rows[1]["meaning"], "예문에서 쓰인 뜻.")
            self.assertTrue(rows[1]["meaning_generated_with_ollama"])
            self.assertTrue(all(row["curation_status"] == "confirmed" for row in rows))

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("15_final_dictionary.jsonl", summary["outputs"])
            self.assertEqual(summary["reviewed_missing_vocabulary_meanings"], 1)
            self.assertEqual(summary["generated_vocabulary_meanings"], 1)
            self.assertEqual(summary["preserved_vocabulary_meanings"], 1)
            self.assertEqual(summary["remaining_missing_vocabulary_meanings"], 0)
            self.assertIn("15", {stage["stage"] for stage in summary["pipeline_stages"]})
            self.assertIn("15_final_dictionary.jsonl", stdout.getvalue())

    def test_main_with_generate_missing_meanings_skips_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)
            (output_dir / "14_vocabulary_with_meanings.jsonl").write_text(
                json.dumps(
                    {
                        "canonical_key": "없는말",
                        "display": "없는말",
                        "example": "없는말 예문",
                        "meaning": "",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout), patch("wordbyword.cli.app.run_pipeline") as mock_pipeline, patch(
                "wordbyword.cli.app.ollama_client",
                return_value=GeneratedMeaningClient(),
            ):
                exit_code = main(["--generate-missing-meanings", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertIn("15_final_dictionary.jsonl", stdout.getvalue())

    def test_generate_missing_meanings_reports_client_configuration_errors(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr), patch(
            "wordbyword.cli.app.ollama_client",
            side_effect=LLMConfigurationError("bad ollama config"),
        ):
            exit_code = generate_missing_meanings(
                output_dir=Path("build-dictionary"),
                model="gemma4:31b",
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("bad ollama config", stderr.getvalue())

    def test_main_with_generate_missing_examples_uses_saved_recovery_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)

            with patch("wordbyword.cli.app.run_saved_dictionary_cleanup") as recovery:
                exit_code = main(["--generate-missing-examples", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            recovery.assert_called_once()
            self.assertTrue(recovery.call_args.kwargs["generate_missing_examples"])

    def test_main_with_retry_generated_examples_uses_saved_recovery_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            output_dir = Path(workspace)

            with patch("wordbyword.cli.app.run_saved_dictionary_cleanup") as recovery:
                exit_code = main(["--retry-generated-examples", "--output", str(output_dir)])

            self.assertEqual(exit_code, 0)
            recovery.assert_called_once()
            self.assertTrue(recovery.call_args.kwargs["retry_generated_examples"])
            self.assertFalse(recovery.call_args.kwargs["generate_missing_examples"])

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


class DocumentationCommandTest(unittest.TestCase):
    def test_documented_wordbyword_commands_parse(self) -> None:
        root = Path(__file__).resolve().parents[1]
        command_lines: list[tuple[Path, str]] = []
        for path in (root / "README.md", root / "BEGINNERS_GUIDE.md"):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("uv ") and "python -m wordbyword" in stripped:
                    command_lines.append((path, stripped))

        self.assertGreater(len(command_lines), 0)
        parser = build_parser()
        for path, command in command_lines:
            with self.subTest(path=path.name, command=command):
                parts = shlex.split(command)
                cli_args = parts[parts.index("wordbyword") + 1 :]
                parser.parse_args(cli_args)


def pipeline_result(output_dir: Path) -> PipelineResult:
    return PipelineResult(
        documents=0,
        extracted_pages=0,
        sentences=0,
        token_occurrences=0,
        vocabulary_terms=0,
        surface_words=0,
        output_dir=output_dir,
    )


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


class GeneratedMeaningClient:
    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        lines = [f"{row['id']}\t예문에서 쓰인 뜻." for row in rows]
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines),
        )


if __name__ == "__main__":
    unittest.main()
