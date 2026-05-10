from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from wordbyword.class_printing.printable import DEFAULT_PRINT_OUTPUT_ROOT
from wordbyword.class_rewriting.rewrite import (
    DEFAULT_REWRITE_OUTPUT_ROOT,
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
)
from wordbyword.class_archive.archive import DEFAULT_CLASS_SESSION_ARCHIVE_ROOT
from wordbyword.class_archive.live_lexicon import (
    DEFAULT_CANONICAL_DICTIONARY_PATH,
    DEFAULT_LIVE_LEXICON_PATH,
    DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
    DEFAULT_USAGE_CANDIDATES_PATH,
)
from wordbyword.class_archive.usage_signals import DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT
from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL
from wordbyword.source_parsing.materials import DEFAULT_PARSED_SOURCE_ROOT, DEFAULT_SOURCE_MATERIAL_ROOT, PdfParserName
from wordbyword.source_parsing.paddleocr_vl import PaddleOCRVLSettings

DEFAULT_OUTPUT_DIR = Path("build-dictionary")
DEFAULT_INPUT_DIR = DEFAULT_OUTPUT_DIR / "original"
DEFAULT_REFERENCE_DICTIONARY_PATH = DEFAULT_OUTPUT_DIR / "references" / "reference-dictionary.jsonl"
PIPELINE_CLEANUP_FLAGS = (
    "clean_example_sentences",
    "clean_dictionary_candidates",
    "generate_missing_examples",
    "retry_generated_examples",
)


@dataclass(frozen=True)
class SourceParseOptions:
    output_root: Path
    parse_pdf: bool
    pdf_parser: PdfParserName
    render_pages: bool
    render_dpi: int
    image_dpi: int
    paddleocr_vl_settings: PaddleOCRVLSettings | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wordbyword",
        description="Extract Korean vocabulary candidates from PDFs and text documents.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Folder containing source PDFs, .txt, or .md files. Defaults to ./build-dictionary/original.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Folder where dictionary build artifacts are written. Defaults to ./build-dictionary.",
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
            "15_final_dictionary.jsonl, asking Ollama to write "
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
        "--ollama-model",
        default=DEFAULT_EXAMPLE_CLEANUP_MODEL,
        help=(
            "Ollama model used for the cleanup stages. "
            f"Defaults to {DEFAULT_EXAMPLE_CLEANUP_MODEL}."
        ),
    )
    parser.add_argument(
        "--ollama-base-url",
        default=None,
        help="Optional Ollama base URL override. Defaults to WORDBYWORD_OLLAMA_BASE_URL or http://localhost:11434.",
    )
    parser.add_argument(
        "--show-distribution",
        nargs="?",
        const="",
        default=None,
        metavar="JSONL",
        help=(
            "Render the cyberpunk distribution panel for an existing "
            "11_cleaned_dictionary_candidates.jsonl and exit. Optional path; "
            "defaults to <output-dir>/11_cleaned_dictionary_candidates.jsonl."
        ),
    )
    parser.add_argument(
        "--parse-source-material",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Parse one class source material into a source-specific workspace. "
            "PDFs are converted to Markdown-first heuristic parse artifacts."
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
            "source-specific workspaces. Defaults to class-source-materials when no DIR is supplied."
        ),
    )
    parser.add_argument(
        "--skip-pdf-parse",
        action="store_true",
        help="Only prepare source-material workspaces; do not run the heuristic PDF parser.",
    )
    parser.add_argument(
        "--pdf-parser",
        choices=("heuristic", "paddleocr-vl"),
        default="heuristic",
        help=(
            "PDF parser to use for source materials. Defaults to heuristic "
            "PyMuPDF4LLM parsing; paddleocr-vl renders each PDF page to PNG and "
            "runs PaddleOCR-VL-1.5."
        ),
    )
    parser.add_argument(
        "--no-page-renders",
        action="store_true",
        help="Do not render full-page PNG review assets for parsed PDFs.",
    )
    parser.add_argument(
        "--pdf-render-dpi",
        type=int,
        default=144,
        help="DPI for full-page PNG review assets. Defaults to 144.",
    )
    parser.add_argument(
        "--pdf-image-dpi",
        type=int,
        default=180,
        help="DPI for images extracted by PyMuPDF4LLM. Defaults to 180.",
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
        help='Optional VLM recognition service model name, for example "PaddlePaddle/PaddleOCR-VL-1.5".',
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
        help=(
            "Root folder for source-material parse bundles. "
            "Defaults to class-source-materials/parsed."
        ),
    )
    parser.add_argument(
        "--source-id",
        default=None,
        help="Optional stable id/folder name for --parse-source-material.",
    )
    parser.add_argument(
        "--rewrite-source-material",
        type=Path,
        default=None,
        metavar="PARSED_BUNDLE_DIR",
        help=(
            "Rewrite one parsed source-material bundle for Korean 1학년 readers. "
            "The path should be a class-source-materials/parsed/<source-id> folder."
        ),
    )
    parser.add_argument(
        "--rewrite-source-materials",
        type=Path,
        nargs="?",
        const=DEFAULT_PARSED_SOURCE_ROOT,
        default=None,
        metavar="PARSED_ROOT",
        help=(
            "Rewrite every parsed source-material bundle under a parsed root for Korean 1학년 readers. "
            "Defaults to class-source-materials/parsed when no root is supplied."
        ),
    )
    parser.add_argument(
        "--rewrite-whole-source-material",
        type=Path,
        default=None,
        metavar="PARSED_BUNDLE_DIR",
        help=(
            "Rewrite one parsed source-material bundle in one whole-document Ollama request. "
            "The path should be a class-source-materials/parsed/<source-id> folder."
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
            "whole-document Ollama request per source. Defaults to class-source-materials/parsed "
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
            "Ollama workflow. The path should be a class-source-materials/parsed/<source-id> folder."
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
            "Defaults to class-source-materials/parsed when no root is supplied."
        ),
    )
    parser.add_argument(
        "--rewrite-dictionary",
        type=Path,
        default=None,
        help=(
            "Dictionary JSONL for vocabulary-guided rewrites. Defaults to "
            "<--output>/15_final_dictionary.jsonl."
        ),
    )
    parser.add_argument(
        "--rewrite-output-root",
        type=Path,
        default=None,
        help=(
            "Root folder for rewritten source-material outputs. Defaults to "
            f"{DEFAULT_REWRITE_OUTPUT_ROOT} for page rewrites and "
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
        default=None,
        metavar="REWRITTEN_ROOT",
        help=(
            "Create editable A4 DOCX printable reading materials for every rewritten bundle under a root."
        ),
    )
    parser.add_argument(
        "--print-output-root",
        type=Path,
        default=DEFAULT_PRINT_OUTPUT_ROOT,
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
        "--rename-class-session-archive",
        type=Path,
        nargs="?",
        const=DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
        default=None,
        metavar="DIR",
        help=(
            "Classify and safely rename direct .docx/.pdf files in a class-session archive. "
            "Defaults to wbw-vocabulary-intelligence/class-session-archive when no DIR is supplied. Dry-run unless "
            "--apply-archive-renames is also supplied."
        ),
    )
    parser.add_argument(
        "--apply-archive-renames",
        action="store_true",
        help="Apply --rename-class-session-archive rename plans. Without this flag, the command is a dry-run.",
    )
    parser.add_argument(
        "--build-usage-signals",
        action="store_true",
        help=(
            "Build deterministic 00-09 vocabulary usage signal artifacts from named class printouts. "
            "Reads wbw-vocabulary-intelligence/class-session-archive and writes "
            "wbw-vocabulary-intelligence/usage-signal-build by default."
        ),
    )
    parser.add_argument(
        "--usage-signal-input-root",
        type=Path,
        default=DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
        help=(
            "Folder of named class printouts for --build-usage-signals. "
            "Defaults to wbw-vocabulary-intelligence/class-session-archive."
        ),
    )
    parser.add_argument(
        "--usage-signal-output-root",
        type=Path,
        default=DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT,
        help=(
            "Output folder for --build-usage-signals artifacts. "
            "Defaults to wbw-vocabulary-intelligence/usage-signal-build."
        ),
    )
    parser.add_argument(
        "--update-live-lexicon",
        action="store_true",
        help=(
            "Regenerate the canonical live grade lexicon from build-dictionary/15_final_dictionary.jsonl "
            "and overlay class usage counts and ROI from "
            "wbw-vocabulary-intelligence/usage-signal-build/08_dictionary_candidates.jsonl."
        ),
    )
    parser.add_argument(
        "--canonical-dictionary",
        type=Path,
        default=DEFAULT_CANONICAL_DICTIONARY_PATH,
        help=(
            "Canonical stage-15 dictionary JSONL for --update-live-lexicon. "
            "Defaults to build-dictionary/15_final_dictionary.jsonl."
        ),
    )
    parser.add_argument(
        "--usage-candidates",
        type=Path,
        default=DEFAULT_USAGE_CANDIDATES_PATH,
        help=(
            "Usage candidate JSONL for --update-live-lexicon. "
            "Defaults to wbw-vocabulary-intelligence/usage-signal-build/08_dictionary_candidates.jsonl."
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
        help=(
            "Canonical live lexicon JSONL to regenerate with class usage signals. "
            "Defaults to wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl."
        ),
    )
    parser.add_argument(
        "--unmatched-usage-output",
        type=Path,
        default=DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
        help=(
            "Output JSONL for usage candidates not found in the live lexicon. "
            "Defaults to wbw-vocabulary-intelligence/usage-signal-build/10_unmatched_usage_candidates.jsonl."
        ),
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    immediate_commands = [
        args.parse_source_material is not None,
        args.parse_source_materials is not None,
        args.rewrite_source_material is not None,
        args.rewrite_source_materials is not None,
        args.rewrite_whole_source_material is not None,
        args.rewrite_whole_source_materials is not None,
        args.rewrite_vocabulary_guided_source_material is not None,
        args.rewrite_vocabulary_guided_source_materials is not None,
        args.print_class_material is not None,
        args.print_class_materials is not None,
        args.rename_class_session_archive is not None,
        args.build_usage_signals,
        args.update_live_lexicon,
    ]
    if sum(1 for requested in immediate_commands if requested) > 1:
        parser.exit(
            2,
            "wordbyword: error: parse/rewrite/print source-material commands cannot be combined "
            "with each other or archive commands.\n",
        )
    if args.apply_archive_renames and args.rename_class_session_archive is None:
        parser.exit(
            2,
            "wordbyword: error: --apply-archive-renames requires --rename-class-session-archive.\n",
        )
    if args.parse_source_materials is not None and args.source_id:
        parser.exit(
            2,
            "wordbyword: error: --source-id can only be used with --parse-source-material.\n",
        )
    if (
        args.rewrite_source_material is not None
        or args.rewrite_source_materials is not None
        or args.rewrite_whole_source_material is not None
        or args.rewrite_whole_source_materials is not None
        or args.rewrite_vocabulary_guided_source_material is not None
        or args.rewrite_vocabulary_guided_source_materials is not None
        or args.print_class_material is not None
        or args.print_class_materials is not None
    ) and args.source_id:
        parser.exit(
            2,
            "wordbyword: error: --source-id can only be used with --parse-source-material.\n",
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
