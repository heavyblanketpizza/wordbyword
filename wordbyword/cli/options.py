from __future__ import annotations

import argparse
from pathlib import Path

from wordbyword.after_class_reports import DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.class_printing.printable import DEFAULT_PRINT_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
)
from wordbyword.class_archive.archive import DEFAULT_CLASS_SESSION_ARCHIVE_ROOT, is_managed_archive_name
from wordbyword.class_archive.live_lexicon import (
    DEFAULT_CANONICAL_DICTIONARY_PATH,
    DEFAULT_LIVE_LEXICON_PATH,
    DEFAULT_SUPPLEMENTAL_LEXICON_PATH,
    DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
    DEFAULT_USAGE_CANDIDATES_PATH,
)
from wordbyword.class_archive.usage_signals import DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT
from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL
from wordbyword.dictionary_build.source_archive import DEFAULT_DICTIONARY_SOURCE_ROOT
from wordbyword.classroom_printout_studio.source_parsing.materials import DEFAULT_PARSED_SOURCE_ROOT, DEFAULT_SOURCE_MATERIAL_ROOT
from wordbyword.application.workspace import SourceParseOptions as SourceParseOptions
from wordbyword.grade_profiles import (
    DEFAULT_TARGET_GRADE,
    REFERENCE_DICTIONARY_PATH,
    VALID_TARGET_GRADES,
    grade_workspace,
    normalize_target_grade,
    path_mentions_other_grade,
)

DEFAULT_WORKSPACE = grade_workspace(DEFAULT_TARGET_GRADE)
DEFAULT_OUTPUT_DIR = DEFAULT_WORKSPACE.dictionary_output_dir
DEFAULT_INPUT_DIR = DEFAULT_WORKSPACE.dictionary_input_dir
DEFAULT_REFERENCE_DICTIONARY_PATH = REFERENCE_DICTIONARY_PATH
PIPELINE_CLEANUP_FLAGS = (
    "clean_example_sentences",
    "clean_dictionary_candidates",
    "generate_missing_examples",
    "retry_generated_examples",
)


class ExplicitPathAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_explicit", True)


class ExplicitValueAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_explicit", True)


class TargetGradeAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        try:
            target_grade = normalize_target_grade(values)
        except ValueError as error:
            parser.error(str(error))
        setattr(namespace, self.dest, target_grade)
        setattr(namespace, "target_grade_explicit", True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wordbyword",
        description="Extract Korean vocabulary candidates from PDFs and text documents.",
    )
    parser.set_defaults(
        target_grade_explicit=False,
        input_explicit=False,
        output_explicit=False,
        source_output_root_explicit=False,
        usage_signal_input_root_explicit=False,
        canonical_dictionary_explicit=False,
        rewrite_dictionary_explicit=False,
        rewrite_output_root_explicit=False,
        print_output_root_explicit=False,
        usage_signal_output_root_explicit=False,
        usage_candidates_explicit=False,
        live_lexicon_explicit=False,
        supplemental_lexicon_explicit=False,
        unmatched_usage_output_explicit=False,
        after_class_report_output_root_explicit=False,
        llm_provider_explicit=False,
        llm_model_explicit=False,
        llm_base_url_explicit=False,
        ollama_model_explicit=False,
        ollama_base_url_explicit=False,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        action=ExplicitPathAction,
        help="Folder containing source PDFs, .txt, or .md files. Defaults to the selected grade workspace.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        action=ExplicitPathAction,
        help="Folder where dictionary build artifacts are written. Defaults to the selected grade workspace.",
    )
    parser.add_argument(
        "--include-punctuation",
        action="store_true",
        help="Include punctuation and symbol tokens in 03_tokens.csv.",
    )
    parser.add_argument(
        "--include-numbers",
        action="store_true",
        help="Include pure number terms in vocabulary and surface word outputs.",
    )
    parser.add_argument(
        "--build-dictionary",
        action="store_true",
        help="Build dictionary stages 00 through 15 in one strict workflow.",
    )
    parser.add_argument(
        "--prep-dictionary",
        action="store_true",
        help="Build deterministic dictionary preparation stages 00 through 09 without LLM calls.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --prep-dictionary or --build-dictionary, ignore saved checkpoints and rebuild requested stages.",
    )
    parser.add_argument(
        "--clean-example-sentences",
        action="store_true",
        help="Use saved build-dictionary/09_example_sentences.csv and build-dictionary/06_candidate_terms.csv to write 10_cleaned_example_sentences.csv.",
    )
    parser.add_argument(
        "--clean-dictionary-candidates",
        action="store_true",
        help="Use saved build-dictionary/08_dictionary_candidates.jsonl and build-dictionary/06_candidate_terms.csv to write 11_cleaned_dictionary_candidates.jsonl.",
    )
    parser.add_argument(
        "--link-cleaned-examples",
        action="store_true",
        help=(
            "Use saved build-dictionary/10_cleaned_example_sentences.csv and "
            "build-dictionary/11_cleaned_dictionary_candidates.jsonl to write "
            "12_dictionary_candidates_with_cleaned_examples.jsonl."
        ),
    )
    parser.add_argument(
        "--generate-missing-examples",
        action="store_true",
        help=(
            "Use saved build-dictionary/12_dictionary_candidates_with_cleaned_examples.jsonl to write "
            "13_vocabulary_with_generated_examples.jsonl, generating a new "
            "example sentence for rows whose example_replaced_with_cleaned_sentence flag is false."
        ),
    )
    parser.add_argument(
        "--retry-generated-examples",
        action="store_true",
        help=(
            "Use saved build-dictionary/13_generated_example_failures.jsonl to run only the stage-13 "
            "repair pass against the unresolved failed subset, updating the 13_* files in place."
        ),
    )
    parser.add_argument(
        "--add-reference-meanings",
        "--add-meanings",
        dest="add_reference_meanings",
        action="store_true",
        help=(
            "Use saved build-dictionary/13_vocabulary_with_generated_examples.jsonl and "
            "build-dictionary/references/reference-dictionary.jsonl to write "
            "14_vocabulary_with_meanings.jsonl."
        ),
    )
    parser.add_argument(
        "--generate-missing-meanings",
        action="store_true",
        help=(
            "Use saved build-dictionary/14_vocabulary_with_meanings.jsonl to write "
            "15_final_dictionary.jsonl, asking the configured LLM to write "
            "Korean meanings for rows whose meaning field is empty."
        ),
    )
    parser.add_argument(
        "--reference-dictionary",
        type=Path,
        default=DEFAULT_REFERENCE_DICTIONARY_PATH,
        help=(
            "Reference JSONL with canonical_key and meaning fields for --add-reference-meanings. "
            "Defaults to build-dictionary/references/reference-dictionary.jsonl."
        ),
    )
    parser.add_argument(
        "--llm-provider",
        default="ollama",
        choices=("ollama", "openai", "anthropic", "claude", "google", "gemini"),
        action=ExplicitValueAction,
        help="LLM provider for model-backed workflows. Defaults to ollama.",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        action=ExplicitValueAction,
        help="LLM model for model-backed workflows. Defaults to the provider-specific model.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        action=ExplicitValueAction,
        help="Optional provider base URL override.",
    )
    parser.add_argument(
        "--ollama-model",
        default=DEFAULT_EXAMPLE_CLEANUP_MODEL,
        action=ExplicitValueAction,
        help=(
            "Backward-compatible alias for --llm-model when --llm-provider is ollama. "
            f"Defaults to {DEFAULT_EXAMPLE_CLEANUP_MODEL}."
        ),
    )
    parser.add_argument(
        "--ollama-base-url",
        default=None,
        action=ExplicitValueAction,
        help=(
            "Backward-compatible alias for --llm-base-url when --llm-provider is ollama. "
            "Defaults to WORDBYWORD_OLLAMA_BASE_URL or http://localhost:11434."
        ),
    )
    parser.add_argument(
        "--target-grade",
        "--grade",
        action=TargetGradeAction,
        default=DEFAULT_TARGET_GRADE,
        metavar="GRADE",
        help=(
            "Korean K-12 target grade for class printouts, usage signals, live lexicons, and reports. "
            f"Use one of {', '.join(VALID_TARGET_GRADES)}. Required for grade-scoped commands."
        ),
    )
    parser.add_argument(
        "--show-distribution",
        nargs="?",
        const="",
        default=None,
        metavar="JSONL",
        help=(
            "Render the cyberpunk distribution panel for the latest saved "
            "dictionary JSONL and exit. Optional path; defaults to "
            "<output-dir>/15_final_dictionary.jsonl when available."
        ),
    )
    parser.add_argument(
        "--rename-dictionary-source-archive",
        type=Path,
        nargs="?",
        const=DEFAULT_DICTIONARY_SOURCE_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Classify and safely rename dictionary source files in a managed source archive. "
            "Defaults to build-dictionary/original when no DIR is supplied. Dry-run unless "
            "--apply-dictionary-source-renames is also supplied."
        ),
    )
    parser.add_argument(
        "--apply-dictionary-source-renames",
        action="store_true",
        help=(
            "Apply --rename-dictionary-source-archive rename plans. Without this flag, "
            "the command is a dry-run."
        ),
    )
    parser.add_argument(
        "--resolve-dictionary-source-duplicates",
        type=Path,
        nargs="?",
        const=DEFAULT_DICTIONARY_SOURCE_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Resolve duplicate .pdf/.txt/.md files in a dictionary source archive by moving "
            "recommended duplicates to a hidden quarantine folder after confirmation. "
            "Defaults to build-dictionary/original when no DIR is supplied."
        ),
    )
    parser.add_argument(
        "--parse-source-material",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Parse one class source material into a source-specific workspace. "
            "PDFs are parsed with PaddleOCR-VL by default."
        ),
    )
    parser.add_argument(
        "--parse-source-materials",
        type=Path,
        nargs="?",
        const=DEFAULT_SOURCE_MATERIAL_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Parse every supported file in a class source-material directory into separate "
            "source-specific workspaces. Defaults to classroom-printout-studio/class-source-materials when no DIR is supplied."
        ),
    )
    parser.add_argument(
        "--skip-pdf-parse",
        action="store_true",
        help="Only prepare source-material workspaces; do not run the PDF parser.",
    )
    parser.add_argument(
        "--pdf-render-dpi",
        type=int,
        default=144,
        help="DPI for PDF page PNG inputs sent to PaddleOCR-VL. Defaults to 144.",
    )
    parser.add_argument(
        "--paddleocr-vl-device",
        default=None,
        help='Optional PaddleOCR-VL inference device, for example "cpu" or "gpu:0".',
    )
    parser.add_argument(
        "--paddleocr-vl-engine",
        default=None,
        help='Optional PaddleOCR-VL engine, for example "paddle" or "transformers".',
    )
    parser.add_argument(
        "--paddleocr-vl-rec-backend",
        default=None,
        help='Optional VLM recognition backend, for example "vllm-server".',
    )
    parser.add_argument(
        "--paddleocr-vl-rec-server-url",
        default=None,
        help="Optional VLM recognition service URL.",
    )
    parser.add_argument(
        "--paddleocr-vl-rec-api-model-name",
        default=None,
        help='Optional VLM recognition service model name, for example "PaddlePaddle/PaddleOCR-VL-1.6".',
    )
    parser.add_argument(
        "--paddleocr-vl-rec-api-key",
        default=None,
        help="Optional VLM recognition service API key.",
    )
    parser.add_argument(
        "--paddleocr-vl-doc-orientation",
        action="store_true",
        help="Enable PaddleOCR-VL document orientation classification.",
    )
    parser.add_argument(
        "--paddleocr-vl-doc-unwarping",
        action="store_true",
        help="Enable PaddleOCR-VL document unwarping.",
    )
    parser.add_argument(
        "--paddleocr-vl-no-layout-detection",
        action="store_true",
        help="Disable PaddleOCR-VL layout detection and send rendered pages directly to the VL recognizer.",
    )
    parser.add_argument(
        "--paddleocr-vl-chart-recognition",
        action="store_true",
        help="Enable PaddleOCR-VL chart recognition.",
    )
    parser.add_argument(
        "--paddleocr-vl-seal-recognition",
        action="store_true",
        help="Enable PaddleOCR-VL seal recognition.",
    )
    parser.add_argument(
        "--paddleocr-vl-no-ocr-for-image-block",
        action="store_true",
        help="Disable PaddleOCR-VL OCR inside detected image blocks.",
    )
    parser.add_argument(
        "--paddleocr-vl-max-new-tokens",
        type=int,
        default=None,
        help="Optional max_new_tokens override for PaddleOCR-VL.",
    )
    parser.add_argument(
        "--paddleocr-vl-sidecar-env",
        type=Path,
        default=None,
        help=(
            "Run PaddleOCR-VL in a separate Python environment. The environment's "
            "bin/python or Scripts/python.exe is used as the sidecar worker."
        ),
    )
    parser.add_argument(
        "--paddleocr-vl-sidecar-python",
        type=Path,
        default=None,
        help="Run PaddleOCR-VL in a sidecar using this Python executable.",
    )
    parser.add_argument(
        "--paddleocr-vl-sidecar-timeout-seconds",
        type=float,
        default=None,
        help="Optional timeout for one PaddleOCR-VL sidecar PDF parse.",
    )
    parser.add_argument(
        "--source-output-root",
        type=Path,
        default=DEFAULT_PARSED_SOURCE_ROOT,
        action=ExplicitPathAction,
        help=(
            "Root folder for source-material parse bundles. "
            "Defaults to classroom-printout-studio/class-source-materials/parsed."
        ),
    )
    parser.add_argument(
        "--source-id",
        default=None,
        help="Optional stable id/folder name for --parse-source-material.",
    )
    parser.add_argument(
        "--rewrite-whole-source-material",
        type=Path,
        default=None,
        metavar="PARSED_BUNDLE_DIR",
        help=(
            "Rewrite one parsed source-material bundle in one whole-document LLM request. "
            "The path should be a classroom-printout-studio/class-source-materials/parsed/<source-id> folder."
        ),
    )
    parser.add_argument(
        "--rewrite-whole-source-materials",
        type=Path,
        nargs="?",
        const=DEFAULT_PARSED_SOURCE_ROOT,
        default=None,
        metavar="PARSED_ROOT",
        help=(
            "Rewrite every parsed source-material bundle under a parsed root in one "
            "whole-document LLM request per source. Defaults to classroom-printout-studio/class-source-materials/parsed "
            "when no root is supplied."
        ),
    )
    parser.add_argument(
        "--rewrite-vocabulary-guided-source-material",
        type=Path,
        default=None,
        metavar="PARSED_BUNDLE_DIR",
        help=(
            "Rewrite one parsed source-material bundle in one vocabulary-guided whole-document "
            "LLM workflow. The path should be a classroom-printout-studio/class-source-materials/parsed/<source-id> folder."
        ),
    )
    parser.add_argument(
        "--rewrite-vocabulary-guided-source-materials",
        type=Path,
        nargs="?",
        const=DEFAULT_PARSED_SOURCE_ROOT,
        default=None,
        metavar="PARSED_ROOT",
        help=(
            "Rewrite every parsed source-material bundle under a parsed root with vocabulary guidance. "
            "Defaults to classroom-printout-studio/class-source-materials/parsed when no root is supplied."
        ),
    )
    parser.add_argument(
        "--rewrite-dictionary",
        type=Path,
        default=None,
        action=ExplicitPathAction,
        help=(
            "Dictionary or canonical live-lexicon JSONL for vocabulary-guided rewrites. Defaults to the "
            "canonical live lexicon when it exists, otherwise <--output>/15_final_dictionary.jsonl."
        ),
    )
    parser.add_argument(
        "--rewrite-output-root",
        type=Path,
        default=None,
        action=ExplicitPathAction,
        help=(
            "Root folder for rewritten source-material outputs. Defaults to "
            f"{DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT} for whole-document rewrites, and "
            f"{DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT} for vocabulary-guided rewrites."
        ),
    )
    parser.add_argument(
        "--print-class-material",
        type=Path,
        default=None,
        metavar="REWRITTEN_BUNDLE_DIR",
        help=(
            "Create an editable A4 DOCX printable reading material from one rewritten source-material bundle."
        ),
    )
    parser.add_argument(
        "--print-class-materials",
        type=Path,
        nargs="?",
        const=DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
        default=None,
        metavar="REWRITTEN_ROOT",
        help=(
            "Create editable A4 DOCX printable reading materials for every rewritten bundle under a root. "
            f"Defaults to {DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT} when no root is supplied."
        ),
    )
    parser.add_argument(
        "--build-class-printouts",
        type=Path,
        nargs="?",
        const=DEFAULT_SOURCE_MATERIAL_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Parse class source materials, rewrite them with vocabulary guidance, and write DOCX printouts. "
            f"Defaults to {DEFAULT_SOURCE_MATERIAL_ROOT} when no DIR is supplied."
        ),
    )
    parser.add_argument(
        "--force-class-printouts",
        action="store_true",
        help="With --build-class-printouts, regenerate sources even when the class-printout ledger says they were already printed.",
    )
    parser.add_argument(
        "--print-output-root",
        type=Path,
        default=DEFAULT_PRINT_OUTPUT_ROOT,
        action=ExplicitPathAction,
        help=(
            "Root folder for printable class-material outputs. "
            f"Defaults to {DEFAULT_PRINT_OUTPUT_ROOT}."
        ),
    )
    parser.add_argument(
        "--print-font-path",
        type=Path,
        default=None,
        help="Optional Korean-capable font file for printable DOCX rendering.",
    )
    parser.add_argument(
        "--print-body-font-size",
        type=int,
        default=None,
        help="Optional printable body font size override from 11 to 15 points.",
    )
    parser.add_argument(
        "--migrate-grade-workspaces",
        action="store_true",
        help=(
            "Preview migration from legacy shared folders into per-grade workspaces. "
            "Use --apply to perform the planned moves."
        ),
    )
    parser.add_argument(
        "--apply",
        dest="apply_grade_workspace_migration",
        action="store_true",
        help="Apply --migrate-grade-workspaces. Without this flag, the migration is a dry-run preview.",
    )
    parser.add_argument(
        "--generate-after-class-reading-report",
        type=Path,
        default=None,
        metavar="CLASS_PRINTOUT",
        help=(
            "Create one adult-facing after-class Korean reading report from one managed archived .docx/.pdf "
            "class printout. Bare filenames are resolved inside the selected grade archive; paths are used exactly "
            "as supplied."
        ),
    )
    parser.add_argument(
        "--after-class-report-output-root",
        type=Path,
        default=DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT,
        action=ExplicitPathAction,
        help=(
            "Root folder for after-class reading report outputs. "
            f"Defaults to {DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT}."
        ),
    )
    parser.add_argument(
        "--after-class-report-font-path",
        type=Path,
        default=None,
        help="Optional Korean-capable font file for after-class DOCX report rendering.",
    )
    parser.add_argument(
        "--rename-class-session-archive",
        type=Path,
        nargs="?",
        const=DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Classify and safely rename direct .docx/.pdf files in a class-session archive. "
            "Defaults to wbw-vocabulary-intelligence/class-session-archive/<grade> when no DIR is supplied. Dry-run "
            "unless --apply-archive-renames is also supplied."
        ),
    )
    parser.add_argument(
        "--apply-archive-renames",
        action="store_true",
        help="Apply --rename-class-session-archive rename plans. Without this flag, the command is a dry-run.",
    )
    parser.add_argument(
        "--resolve-archive-duplicates",
        type=Path,
        nargs="?",
        const=DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Resolve duplicate direct .docx/.pdf files in a class-session archive by moving recommended duplicates "
            "to a hidden quarantine folder after confirmation. Defaults to wbw-vocabulary-intelligence/"
            "class-session-archive/<grade> when no DIR is supplied."
        ),
    )
    parser.add_argument(
        "--include-near-duplicates",
        action="store_true",
        help=(
            "With --resolve-archive-duplicates, also ask the configured LLM to review conservative near-duplicate "
            "text candidates before recommending quarantines."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="With --resolve-archive-duplicates, apply the recommended quarantine plan without prompting.",
    )
    parser.add_argument(
        "--build-usage-signals",
        action="store_true",
        help=(
            "Build deterministic 00-09 vocabulary usage signal artifacts from named class printouts. "
            "Reads wbw-vocabulary-intelligence/class-session-archive/<grade> and writes "
            "wbw-vocabulary-intelligence/usage-signal-build/<grade> by default."
        ),
    )
    parser.add_argument(
        "--usage-signal-input-root",
        type=Path,
        default=DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
        action=ExplicitPathAction,
        help=(
            "Folder of named class printouts for --build-usage-signals. "
            "Defaults to wbw-vocabulary-intelligence/class-session-archive/<grade>."
        ),
    )
    parser.add_argument(
        "--usage-signal-output-root",
        type=Path,
        default=DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT,
        action=ExplicitPathAction,
        help=(
            "Output folder for --build-usage-signals artifacts. "
            "Defaults to wbw-vocabulary-intelligence/usage-signal-build/<grade>."
        ),
    )
    parser.add_argument(
        "--update-live-lexicon",
        action="store_true",
        help=(
            "Maintain canonical and supplemental live grade lexicons with class usage counts and ROI from "
            "wbw-vocabulary-intelligence/usage-signal-build/<grade>/08_dictionary_candidates.jsonl."
        ),
    )
    parser.add_argument(
        "--canonical-dictionary",
        type=Path,
        default=DEFAULT_CANONICAL_DICTIONARY_PATH,
        action=ExplicitPathAction,
        help=(
            "Canonical stage-15 dictionary JSONL for --update-live-lexicon. "
            "Defaults to build-dictionary/output/<grade>/15_final_dictionary.jsonl."
        ),
    )
    parser.add_argument(
        "--usage-candidates",
        type=Path,
        default=DEFAULT_USAGE_CANDIDATES_PATH,
        action=ExplicitPathAction,
        help=(
            "Usage candidate JSONL for --update-live-lexicon. "
            "Defaults to wbw-vocabulary-intelligence/usage-signal-build/<grade>/08_dictionary_candidates.jsonl."
        ),
    )
    parser.add_argument(
        "--usage-summary",
        type=Path,
        default=None,
        help=(
            "Usage signal 00_summary.json for --update-live-lexicon. "
            "Defaults to 00_summary.json next to --usage-candidates."
        ),
    )
    parser.add_argument(
        "--live-lexicon",
        type=Path,
        default=DEFAULT_LIVE_LEXICON_PATH,
        action=ExplicitPathAction,
        help=(
            "Canonical live lexicon JSONL to maintain with class usage signals. "
            "Defaults to wbw-vocabulary-intelligence/live-grade-lexicon/<grade>/canonical.jsonl."
        ),
    )
    parser.add_argument(
        "--supplemental-lexicon",
        type=Path,
        default=DEFAULT_SUPPLEMENTAL_LEXICON_PATH,
        action=ExplicitPathAction,
        help=(
            "Supplemental non-canonical live lexicon JSONL to maintain from unmatched class usage. "
            "Defaults to wbw-vocabulary-intelligence/live-grade-lexicon/<grade>/supplemental.jsonl."
        ),
    )
    parser.add_argument(
        "--unmatched-usage-output",
        type=Path,
        default=DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
        action=ExplicitPathAction,
        help=(
            "Output JSONL for usage candidates not found in the live lexicon. "
            "Defaults to wbw-vocabulary-intelligence/usage-signal-build/<grade>/10_unmatched_usage_candidates.jsonl."
        ),
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    immediate_commands = [
        args.migrate_grade_workspaces,
        args.parse_source_material is not None,
        args.parse_source_materials is not None,
        args.rewrite_whole_source_material is not None,
        args.rewrite_whole_source_materials is not None,
        args.rewrite_vocabulary_guided_source_material is not None,
        args.rewrite_vocabulary_guided_source_materials is not None,
        args.print_class_material is not None,
        args.print_class_materials is not None,
        args.build_class_printouts is not None,
        args.generate_after_class_reading_report is not None,
        args.rename_dictionary_source_archive is not None,
        args.resolve_dictionary_source_duplicates is not None,
        args.rename_class_session_archive is not None,
        args.resolve_archive_duplicates is not None,
        args.build_usage_signals,
        args.update_live_lexicon,
    ]
    dictionary_stage_commands = [
        args.clean_example_sentences,
        args.clean_dictionary_candidates,
        args.link_cleaned_examples,
        args.generate_missing_examples,
        args.retry_generated_examples,
        args.add_reference_meanings,
        args.generate_missing_meanings,
    ]
    if args.llm_provider != "ollama" and (
        bool(getattr(args, "ollama_model_explicit", False))
        or bool(getattr(args, "ollama_base_url_explicit", False))
    ):
        parser.exit(
            2,
            "wordbyword: error: --ollama-model and --ollama-base-url can only be used with "
            "--llm-provider ollama. Use --llm-model and --llm-base-url for other providers.\n",
        )
    if bool(getattr(args, "llm_model_explicit", False)) and bool(getattr(args, "ollama_model_explicit", False)):
        parser.exit(
            2,
            "wordbyword: error: choose only one of --llm-model or --ollama-model.\n",
        )
    if bool(getattr(args, "llm_base_url_explicit", False)) and bool(getattr(args, "ollama_base_url_explicit", False)):
        parser.exit(
            2,
            "wordbyword: error: choose only one of --llm-base-url or --ollama-base-url.\n",
        )
    if args.apply_grade_workspace_migration and not args.migrate_grade_workspaces:
        parser.exit(
            2,
            "wordbyword: error: --apply requires --migrate-grade-workspaces.\n",
        )
    if args.force and not (args.prep_dictionary or args.build_dictionary):
        parser.exit(
            2,
            "wordbyword: error: --force can only be used with --prep-dictionary or --build-dictionary.\n",
        )
    if args.force_class_printouts and args.build_class_printouts is None:
        parser.exit(
            2,
            "wordbyword: error: --force-class-printouts requires --build-class-printouts.\n",
        )
    command_requested = any(
        [
            args.prep_dictionary,
            args.build_dictionary,
            *dictionary_stage_commands,
            *immediate_commands,
            args.show_distribution is not None,
        ]
    )
    if not command_requested:
        parser.exit(
            2,
            "wordbyword: error: choose an explicit command: --prep-dictionary for stages 00-09 "
            "or --build-dictionary for the full strict workflow.\n",
        )
    if args.prep_dictionary and args.build_dictionary:
        parser.exit(
            2,
            "wordbyword: error: --prep-dictionary cannot be combined with --build-dictionary.\n",
        )
    if args.migrate_grade_workspaces and any(
        [
            args.prep_dictionary,
            args.build_dictionary,
            *dictionary_stage_commands,
            *immediate_commands[1:],
            args.show_distribution is not None,
        ]
    ):
        parser.exit(
            2,
            "wordbyword: error: --migrate-grade-workspaces cannot be combined with other commands.\n",
        )
    if args.build_dictionary and any(
        dictionary_stage_commands + immediate_commands + [args.show_distribution is not None]
    ):
        parser.exit(
            2,
            "wordbyword: error: --build-dictionary cannot be combined with stage-specific "
            "dictionary commands, distribution display, class printout commands, archive/report "
            "commands, or usage-signal commands.\n",
        )
    if args.prep_dictionary and any(
        dictionary_stage_commands + immediate_commands + [args.show_distribution is not None]
    ):
        parser.exit(
            2,
            "wordbyword: error: --prep-dictionary cannot be combined with stage-specific "
            "dictionary commands, distribution display, class printout commands, archive/report "
            "commands, or usage-signal commands.\n",
        )
    if any(getattr(args, flag_name) for flag_name in PIPELINE_CLEANUP_FLAGS) and any(
        [
            args.link_cleaned_examples,
            args.add_reference_meanings,
            args.generate_missing_meanings,
            *immediate_commands,
            args.show_distribution is not None,
        ]
    ):
        parser.exit(
            2,
            "wordbyword: error: cleanup and generated-example recovery commands cannot be combined "
            "with other commands.\n",
        )
    if sum(1 for requested in immediate_commands if requested) > 1:
        parser.exit(
            2,
            "wordbyword: error: parse/rewrite/print source-material commands cannot be combined "
            "with each other, archive commands, or after-class report generation.\n",
        )
    if args.apply_archive_renames and args.rename_class_session_archive is None:
        parser.exit(
            2,
            "wordbyword: error: --apply-archive-renames requires --rename-class-session-archive.\n",
        )
    if args.apply_dictionary_source_renames and args.rename_dictionary_source_archive is None:
        parser.exit(
            2,
            "wordbyword: error: --apply-dictionary-source-renames requires --rename-dictionary-source-archive.\n",
        )
    if args.yes and args.resolve_archive_duplicates is None and args.resolve_dictionary_source_duplicates is None:
        parser.exit(
            2,
            "wordbyword: error: --yes requires --resolve-archive-duplicates or --resolve-dictionary-source-duplicates.\n",
        )
    if args.include_near_duplicates and args.resolve_archive_duplicates is None:
        parser.exit(
            2,
            "wordbyword: error: --include-near-duplicates requires --resolve-archive-duplicates.\n",
        )
    grade_scoped_command = any(
        [
            args.prep_dictionary,
            args.build_dictionary,
            *dictionary_stage_commands,
            *immediate_commands[1:],
        ]
    )
    if grade_scoped_command and not args.target_grade_explicit:
        parser.exit(
            2,
            "wordbyword: error: --target-grade is required for this grade-scoped command.\n",
        )
    if grade_scoped_command:
        validate_grade_scoped_paths(parser, args)
    if args.parse_source_materials is not None and args.source_id:
        parser.exit(
            2,
            "wordbyword: error: --source-id can only be used with --parse-source-material.\n",
        )
    if (
        args.rewrite_whole_source_material is not None
        or args.rewrite_whole_source_materials is not None
        or args.rewrite_vocabulary_guided_source_material is not None
        or args.rewrite_vocabulary_guided_source_materials is not None
        or args.print_class_material is not None
        or args.print_class_materials is not None
        or args.build_class_printouts is not None
        or args.build_dictionary
        or args.prep_dictionary
    ) and args.source_id:
        parser.exit(
            2,
            "wordbyword: error: --source-id can only be used with --parse-source-material.\n",
        )
    if args.generate_after_class_reading_report is not None and args.source_id:
        parser.exit(
            2,
            "wordbyword: error: --source-id can only be used with --parse-source-material.\n",
        )
    if args.generate_after_class_reading_report is not None:
        args.generate_after_class_reading_report = validate_after_class_report_cli_input(
            parser,
            args.generate_after_class_reading_report,
            target_grade=args.target_grade,
        )
    if args.print_body_font_size is not None and not 11 <= args.print_body_font_size <= 15:
        parser.exit(
            2,
            "wordbyword: error: --print-body-font-size must be between 11 and 15.\n",
        )
    if args.retry_generated_examples and (
        args.clean_example_sentences or args.clean_dictionary_candidates or args.generate_missing_examples
    ):
        parser.exit(
            2,
            "wordbyword: error: --retry-generated-examples cannot be combined with "
            "--clean-example-sentences, --clean-dictionary-candidates, or --generate-missing-examples.\n",
        )


def validate_grade_scoped_paths(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    target_grade = normalize_target_grade(args.target_grade)
    candidate_paths = [
        ("--input", args.input, bool(getattr(args, "input_explicit", False))),
        ("--output", args.output, bool(getattr(args, "output_explicit", False))),
        ("--parse-source-material", args.parse_source_material, args.parse_source_material is not None),
        ("--parse-source-materials", args.parse_source_materials, args.parse_source_materials is not None),
        ("--source-output-root", args.source_output_root, bool(getattr(args, "source_output_root_explicit", False))),
        ("--rewrite-whole-source-material", args.rewrite_whole_source_material, args.rewrite_whole_source_material is not None),
        ("--rewrite-whole-source-materials", args.rewrite_whole_source_materials, args.rewrite_whole_source_materials is not None),
        (
            "--rewrite-vocabulary-guided-source-material",
            args.rewrite_vocabulary_guided_source_material,
            args.rewrite_vocabulary_guided_source_material is not None,
        ),
        (
            "--rewrite-vocabulary-guided-source-materials",
            args.rewrite_vocabulary_guided_source_materials,
            args.rewrite_vocabulary_guided_source_materials is not None,
        ),
        ("--rewrite-dictionary", args.rewrite_dictionary, args.rewrite_dictionary is not None),
        ("--rewrite-output-root", args.rewrite_output_root, args.rewrite_output_root is not None),
        ("--print-class-material", args.print_class_material, args.print_class_material is not None),
        ("--print-class-materials", args.print_class_materials, args.print_class_materials is not None),
        ("--build-class-printouts", args.build_class_printouts, args.build_class_printouts is not None),
        ("--print-output-root", args.print_output_root, bool(getattr(args, "print_output_root_explicit", False))),
        (
            "--generate-after-class-reading-report",
            args.generate_after_class_reading_report,
            args.generate_after_class_reading_report is not None,
        ),
        ("--rename-class-session-archive", args.rename_class_session_archive, args.rename_class_session_archive is not None),
        ("--resolve-archive-duplicates", args.resolve_archive_duplicates, args.resolve_archive_duplicates is not None),
        (
            "--rename-dictionary-source-archive",
            args.rename_dictionary_source_archive,
            args.rename_dictionary_source_archive is not None,
        ),
        (
            "--resolve-dictionary-source-duplicates",
            args.resolve_dictionary_source_duplicates,
            args.resolve_dictionary_source_duplicates is not None,
        ),
        (
            "--after-class-report-output-root",
            args.after_class_report_output_root,
            bool(getattr(args, "after_class_report_output_root_explicit", False)),
        ),
        ("--usage-signal-input-root", args.usage_signal_input_root, bool(getattr(args, "usage_signal_input_root_explicit", False))),
        ("--usage-signal-output-root", args.usage_signal_output_root, bool(getattr(args, "usage_signal_output_root_explicit", False))),
        ("--canonical-dictionary", args.canonical_dictionary, bool(getattr(args, "canonical_dictionary_explicit", False))),
        ("--usage-candidates", args.usage_candidates, bool(getattr(args, "usage_candidates_explicit", False))),
        ("--live-lexicon", args.live_lexicon, bool(getattr(args, "live_lexicon_explicit", False))),
        ("--supplemental-lexicon", args.supplemental_lexicon, bool(getattr(args, "supplemental_lexicon_explicit", False))),
        (
            "--unmatched-usage-output",
            args.unmatched_usage_output,
            bool(getattr(args, "unmatched_usage_output_explicit", False)),
        ),
    ]
    for option, path, explicit in candidate_paths:
        if explicit and path is not None and path_mentions_other_grade(path, target_grade):
            parser.exit(
                2,
                f"wordbyword: error: {option} contains a different grade token than --target-grade {target_grade}: {path}\n",
            )


def resolve_after_class_report_cli_input(path: Path, *, target_grade: str | None = None) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate
    if target_grade is not None:
        return grade_workspace(target_grade).archive_root / candidate
    return DEFAULT_CLASS_SESSION_ARCHIVE_ROOT / candidate


def validate_after_class_report_cli_input(
    parser: argparse.ArgumentParser,
    path: Path,
    *,
    target_grade: str | None = None,
) -> Path:
    resolved_path = resolve_after_class_report_cli_input(path, target_grade=target_grade)
    if not resolved_path.exists():
        parser.exit(2, f"wordbyword: error: after-class report input does not exist: {resolved_path}\n")
    if resolved_path.is_dir():
        parser.exit(
            2,
            f"wordbyword: error: --generate-after-class-reading-report expects one exact .docx or .pdf file, not a directory: {resolved_path}\n",
        )
    if resolved_path.name.startswith("~$"):
        parser.exit(2, f"wordbyword: error: after-class report input is a temporary Office file: {resolved_path}\n")
    if resolved_path.suffix.lower() not in {".docx", ".pdf"}:
        parser.exit(
            2,
            f"wordbyword: error: after-class report input must be a .docx or .pdf file: {resolved_path}\n",
        )
    if not is_managed_archive_name(resolved_path.stem):
        parser.exit(
            2,
            "wordbyword: error: after-class report input must use the managed class-session archive filename pattern.\n"
            "  hint: run --rename-class-session-archive first, then pass the renamed file.\n",
        )
    return resolved_path
