from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupBatchProgress
from wordbyword.dictionary_build.example_generation import GeneratedExampleAttemptSummary
from wordbyword.dictionary_build.llm import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    ollama_client,
    unload_model_if_supported,
)
from wordbyword.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.dictionary_build.pipeline import PipelineUsageError, run_pipeline
from wordbyword.source_parsing.materials import (
    DEFAULT_PARSED_SOURCE_ROOT,
    DEFAULT_SOURCE_MATERIAL_ROOT,
    PdfParserName,
    SourceMaterialParseError,
    parse_source_material,
    parse_source_materials,
)
from wordbyword.class_rewriting.rewrite import (
    DEFAULT_REWRITE_OUTPUT_ROOT,
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
    SourceRewriteError,
    rewrite_source_material as rewrite_parsed_source_material,
    rewrite_source_materials as rewrite_parsed_source_materials,
    rewrite_vocabulary_guided_source_material as rewrite_vocabulary_guided_parsed_source_material,
    rewrite_vocabulary_guided_source_materials as rewrite_vocabulary_guided_parsed_source_materials,
    rewrite_whole_source_material as rewrite_whole_parsed_source_material,
    rewrite_whole_source_materials as rewrite_whole_parsed_source_materials,
)
from wordbyword.class_printing.printable import (
    DEFAULT_PRINT_OUTPUT_ROOT,
    PrintableMaterialError,
    print_class_material,
    print_class_materials,
)
from wordbyword.cli.terminal_ui import (
    emit_artifact_drop,
    emit_boot_screen,
    emit_cleanup_progress,
    emit_completion_screen,
    emit_source_material_progress,
    emit_stage_update,
    render_artifact_drop,
)
from wordbyword.dictionary_build.writers import refresh_saved_output_metadata, write_linked_dictionary_candidates_from_saved
from wordbyword.dictionary_build.saved_outputs import (
    write_generated_meaning_dictionary_candidates_from_saved,
    write_meaning_dictionary_candidates_from_saved,
)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    immediate_exit_code = run_immediate_command(args)
    if immediate_exit_code is not None:
        return immediate_exit_code

    emit_boot_screen(
        input_dir=args.input,
        output_dir=args.output,
        clean_example_sentences=pipeline_cleanup_requested(args),
        sentence_cleanup_model=args.ollama_model,
    )

    try:
        result = run_pipeline(
            input_dir=args.input,
            output_dir=args.output,
            include_punctuation=args.include_punctuation,
            include_numbers=args.include_numbers,
            clean_example_sentences=args.clean_example_sentences,
            clean_dictionary_candidates=args.clean_dictionary_candidates,
            generate_missing_examples=args.generate_missing_examples,
            retry_generated_examples=args.retry_generated_examples,
            sentence_cleanup_model=args.ollama_model,
            sentence_cleanup_base_url=args.ollama_base_url,
            progress=emit_stage_update,
            cleanup_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="example",
                item_label="rows",
            ),
            dictionary_cleanup_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="dictionary",
                item_label="terms",
            ),
            generated_example_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="candidate example",
                item_label="terms",
            ),
            generated_example_retry_decider=generated_example_retry_decider(args),
            artifact_callback=pipeline_artifact_callback(model=args.ollama_model),
        )
    except PipelineUsageError as exc:
        parser.exit(2, f"wordbyword: error: {exc}\n")

    emit_completion_screen(
        result,
        input_dir=args.input,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=pipeline_cleanup_requested(args),
        sentence_cleanup_model=args.ollama_model,
    )
    return 0


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
    ]
    if sum(1 for requested in immediate_commands if requested) > 1:
        parser.exit(
            2,
            "wordbyword: error: parse/rewrite/print source-material commands cannot be combined.\n",
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


def run_immediate_command(args: argparse.Namespace) -> int | None:
    if args.parse_source_material is not None:
        options = source_parse_options_from_args(args)
        return parse_source_material_cli(
            source_path=args.parse_source_material,
            output_root=options.output_root,
            source_id=args.source_id,
            parse_pdf=options.parse_pdf,
            pdf_parser=options.pdf_parser,
            render_pages=options.render_pages,
            render_dpi=options.render_dpi,
            image_dpi=options.image_dpi,
            paddleocr_vl_settings=options.paddleocr_vl_settings,
        )
    if args.parse_source_materials is not None:
        options = source_parse_options_from_args(args)
        return parse_source_materials_cli(
            source_dir=args.parse_source_materials,
            output_root=options.output_root,
            parse_pdf=options.parse_pdf,
            pdf_parser=options.pdf_parser,
            render_pages=options.render_pages,
            render_dpi=options.render_dpi,
            image_dpi=options.image_dpi,
            paddleocr_vl_settings=options.paddleocr_vl_settings,
        )
    if args.rewrite_source_material is not None:
        return rewrite_source_material_cli(
            parsed_bundle_dir=args.rewrite_source_material,
            output_root=args.rewrite_output_root or DEFAULT_REWRITE_OUTPUT_ROOT,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_source_materials is not None:
        return rewrite_source_materials_cli(
            parsed_root=args.rewrite_source_materials,
            output_root=args.rewrite_output_root or DEFAULT_REWRITE_OUTPUT_ROOT,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_whole_source_material is not None:
        return rewrite_whole_source_material_cli(
            parsed_bundle_dir=args.rewrite_whole_source_material,
            output_root=args.rewrite_output_root or DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_whole_source_materials is not None:
        return rewrite_whole_source_materials_cli(
            parsed_root=args.rewrite_whole_source_materials,
            output_root=args.rewrite_output_root or DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_vocabulary_guided_source_material is not None:
        return rewrite_vocabulary_guided_source_material_cli(
            parsed_bundle_dir=args.rewrite_vocabulary_guided_source_material,
            output_root=args.rewrite_output_root or DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
            dictionary_path=rewrite_dictionary_path_from_args(args),
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_vocabulary_guided_source_materials is not None:
        return rewrite_vocabulary_guided_source_materials_cli(
            parsed_root=args.rewrite_vocabulary_guided_source_materials,
            output_root=args.rewrite_output_root or DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
            dictionary_path=rewrite_dictionary_path_from_args(args),
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.print_class_material is not None:
        return print_class_material_cli(
            rewritten_bundle_dir=args.print_class_material,
            output_root=args.print_output_root,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            font_path=args.print_font_path,
            body_font_size=args.print_body_font_size,
        )
    if args.print_class_materials is not None:
        return print_class_materials_cli(
            rewritten_root=args.print_class_materials,
            output_root=args.print_output_root,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            font_path=args.print_font_path,
            body_font_size=args.print_body_font_size,
        )
    if args.show_distribution is not None:
        return show_distribution(
            jsonl_path=resolve_distribution_path(args.show_distribution, args.output),
            model=args.ollama_model,
        )
    if args.link_cleaned_examples:
        return link_cleaned_examples(output_dir=args.output)
    if args.add_reference_meanings:
        return add_reference_meanings(
            output_dir=args.output,
            reference_dictionary_path=args.reference_dictionary,
        )
    if args.generate_missing_meanings:
        return generate_missing_meanings(
            output_dir=args.output,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    return None


def rewrite_dictionary_path_from_args(args: argparse.Namespace) -> Path:
    return args.rewrite_dictionary or (args.output / "15_final_dictionary.jsonl")


def parse_pdf_parser_name(value: str) -> PdfParserName:
    if value not in ("heuristic", "paddleocr-vl"):
        raise PipelineUsageError(f"unsupported PDF parser: {value}")
    return value


def source_parse_options_from_args(args: argparse.Namespace) -> SourceParseOptions:
    return SourceParseOptions(
        output_root=args.source_output_root,
        parse_pdf=not args.skip_pdf_parse,
        pdf_parser=parse_pdf_parser_name(args.pdf_parser),
        render_pages=not args.no_page_renders,
        render_dpi=args.pdf_render_dpi,
        image_dpi=args.pdf_image_dpi,
        paddleocr_vl_settings=paddleocr_vl_settings_from_args(args),
    )


def pipeline_cleanup_requested(args: argparse.Namespace) -> bool:
    return any(bool(getattr(args, flag_name)) for flag_name in PIPELINE_CLEANUP_FLAGS)


def cleanup_progress_callback(
    *,
    model: str,
    label: str,
    item_label: str,
) -> Callable[[CleanupBatchProgress], None]:
    def report(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=model,
            label=label,
            item_label=item_label,
        )

    return report


def generated_example_retry_decider(
    args: argparse.Namespace,
) -> Callable[[GeneratedExampleAttemptSummary], bool]:
    def decide(summary: GeneratedExampleAttemptSummary) -> bool:
        if args.retry_generated_examples:
            return False
        return prompt_generated_example_retry(
            summary=summary,
            output_dir=args.output,
            model=args.ollama_model,
        )

    return decide


def pipeline_artifact_callback(
    *,
    model: str,
) -> Callable[[str, Sequence[Mapping[str, object]]], None]:
    def report(stage: str, entries: Sequence[Mapping[str, object]]) -> None:
        if stage == "09_example_sentences":
            emit_artifact_drop(
                stage,
                file_label="09_example_sentences.csv",
                summary=(
                    ("relics", f"{len(entries):,} sentences"),
                    ("status", "signal locked"),
                ),
            )
            return
        if stage == "11_cleaned_dictionary_candidates":
            emit_artifact_drop(
                stage,
                entries=entries,
                file_label="11_cleaned_dictionary_candidates.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("model", model),
                    ("status", "cache locked"),
                ),
            )
            return
        if stage == "12_dictionary_candidates_with_cleaned_examples":
            replaced_count = count_truthy(entries, "example_replaced_with_cleaned_sentence")
            emit_artifact_drop(
                stage,
                file_label="12_dictionary_candidates_with_cleaned_examples.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("replaced", f"{replaced_count:,} examples"),
                    ("rewrite", f"{len(entries) - replaced_count:,} pending"),
                ),
            )
            return
        if stage == "13_vocabulary_with_generated_examples":
            generated_count = count_truthy(entries, "example_generated_with_ollama")
            emit_artifact_drop(
                stage,
                file_label="13_vocabulary_with_generated_examples.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("generated", f"{generated_count:,} examples"),
                    ("preserved", f"{len(entries) - generated_count:,} existing"),
                    ("model", model),
                ),
            )
            return
        if stage == "14_vocabulary_with_meanings":
            matched_count = count_nonempty(entries, "meaning")
            emit_artifact_drop(
                stage,
                file_label="14_vocabulary_with_meanings.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("matched", f"{matched_count:,} meanings"),
                    ("missing", f"{len(entries) - matched_count:,} meanings"),
                ),
            )
            return
        if stage == "15_final_dictionary":
            generated_count = count_truthy(entries, "meaning_generated_with_ollama")
            emit_artifact_drop(
                stage,
                file_label="15_final_dictionary.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("generated", f"{generated_count:,} meanings"),
                    ("preserved", f"{len(entries) - generated_count:,} meanings"),
                    ("model", model),
                ),
            )

    return report


def count_truthy(entries: Sequence[Mapping[str, object]], key: str) -> int:
    return sum(1 for entry in entries if bool(entry.get(key)))


def count_nonempty(entries: Sequence[Mapping[str, object]], key: str) -> int:
    return sum(1 for entry in entries if bool(str(entry.get(key, "")).strip()))


def resolve_distribution_path(supplied: str, output_dir: Path) -> Path:
    if supplied:
        return Path(supplied)
    return output_dir / "11_cleaned_dictionary_candidates.jsonl"


def load_dictionary_candidates(jsonl_path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {jsonl_path}: {error.msg}"
                ) from error
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Line {line_number} of {jsonl_path} is not a JSON object."
                )
            entries.append(entry)
    return entries


def emit_rendered_artifact_drop(
    stage: str,
    *,
    file_label: str,
    summary: Sequence[tuple[str, str]],
    entries: Sequence[Mapping[str, object]] | None = None,
) -> None:
    rendered = render_artifact_drop(
        stage,
        entries=entries,
        file_label=file_label,
        summary=summary,
        color=sys.stdout.isatty() and "NO_COLOR" not in os.environ,
        width=shutil.get_terminal_size(fallback=(100, 24)).columns,
    )
    sys.stdout.write(rendered)
    sys.stdout.write("\n")
    sys.stdout.flush()


def show_distribution(*, jsonl_path: Path, model: str) -> int:
    if not jsonl_path.is_file():
        sys.stderr.write(
            f"wordbyword: cannot read distribution source: {jsonl_path}\n"
            "  hint: run with --clean-dictionary-candidates first, "
            "or pass a path: --show-distribution path/to/11_cleaned_dictionary_candidates.jsonl\n"
        )
        return 1

    try:
        entries = load_dictionary_candidates(jsonl_path)
    except ValueError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_rendered_artifact_drop(
        "11_cleaned_dictionary_candidates",
        entries=entries,
        file_label=str(jsonl_path),
        summary=(
            ("relics", f"{len(entries):,} headwords"),
            ("model", model),
            ("status", "replay // no llm calls"),
        ),
    )
    return 0


def parse_source_material_cli(
    *,
    source_path: Path,
    output_root: Path,
    source_id: str | None,
    parse_pdf: bool,
    pdf_parser: PdfParserName,
    render_pages: bool,
    render_dpi: int,
    image_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    try:
        bundle = parse_source_material(
            source_path,
            output_root=output_root,
            source_id=source_id,
            parse_pdf=parse_pdf,
            pdf_parser=pdf_parser,
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            progress=emit_source_material_progress,
        )
    except KeyboardInterrupt:
        sys.stderr.write("wordbyword: source-material parsing interrupted; partial workspace cleaned.\n")
        return 130
    except SourceMaterialParseError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    files = bundle.manifest.get("files", {})
    sys.stdout.write(
        "source material parsed\n"
        f"  source_id: {bundle.source_id}\n"
        f"  output_dir: {bundle.output_dir}\n"
        f"  manifest: {bundle.manifest_path}\n"
        f"  assets: {bundle.assets_dir}\n"
        f"  parser_status: {bundle.manifest['parser']['status']}\n"
    )
    for label in ("content_markdown", "quality_report_json", "document_model_json", "chunks_json"):
        if label in files:
            sys.stdout.write(f"  {label}: {bundle.output_dir / str(files[label])}\n")
    sys.stdout.flush()
    return 0


def parse_source_materials_cli(
    *,
    source_dir: Path,
    output_root: Path,
    parse_pdf: bool,
    pdf_parser: PdfParserName,
    render_pages: bool,
    render_dpi: int,
    image_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    try:
        result = parse_source_materials(
            source_dir,
            output_root=output_root,
            parse_pdf=parse_pdf,
            pdf_parser=pdf_parser,
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            progress=emit_source_material_progress,
        )
    except KeyboardInterrupt:
        sys.stderr.write("wordbyword: source-material parsing interrupted; partial workspace cleaned.\n")
        return 130
    except SourceMaterialParseError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "source materials parsed\n"
        f"  source_dir: {result.source_dir}\n"
        f"  output_root: {result.output_root}\n"
        f"  sources: {len(result.source_paths)}\n"
    )
    for bundle in result.bundles:
        files = bundle.manifest.get("files", {})
        content = f", content: {files['content_markdown']}" if "content_markdown" in files else ""
        sys.stdout.write(
            f"  - {bundle.source_id}: {bundle.output_dir} "
            f"(assets: {bundle.assets_dir.name}/, parser: {bundle.manifest['parser']['status']}{content})\n"
        )
    sys.stdout.flush()
    return 0


def rewrite_source_material_cli(
    *,
    parsed_bundle_dir: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=600.0,
        max_output_tokens=8192,
    )
    try:
        try:
            outputs = rewrite_parsed_source_material(
                parsed_bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "source material rewritten\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  rewritten_pages: {outputs.rewritten_page_count}\n"
        f"  failed_pages: {outputs.failed_page_count}\n"
        f"  content: {outputs.output_dir / 'content.md'}\n"
    )
    sys.stdout.flush()
    return 0 if outputs.failed_page_count == 0 else 1


def rewrite_source_materials_cli(
    *,
    parsed_root: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=600.0,
        max_output_tokens=8192,
    )
    try:
        try:
            outputs = rewrite_parsed_source_materials(
                parsed_root,
                output_root=output_root,
                client=client,
                model=model,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
    rewritten_pages = sum(output.rewritten_page_count for output in outputs.source_outputs)
    sys.stdout.write(
        "source materials rewritten\n"
        f"  parsed_root: {outputs.parsed_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  rewritten_pages: {rewritten_pages}\n"
        f"  failed_pages: {failed_pages}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"(rewritten: {output.rewritten_page_count}, failed: {output.failed_page_count})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed_pages == 0 and outputs.source_outputs else 1


def rewrite_whole_source_material_cli(
    *,
    parsed_bundle_dir: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_whole_parsed_source_material(
                parsed_bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "source material rewritten as whole document\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  rewritten_pages: {outputs.rewritten_page_count}\n"
        f"  failed_pages: {outputs.failed_page_count}\n"
        f"  content: {outputs.output_dir / 'content.md'}\n"
    )
    sys.stdout.flush()
    return 0 if outputs.failed_page_count == 0 else 1


def rewrite_whole_source_materials_cli(
    *,
    parsed_root: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_whole_parsed_source_materials(
                parsed_root,
                output_root=output_root,
                client=client,
                model=model,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
    rewritten_pages = sum(output.rewritten_page_count for output in outputs.source_outputs)
    sys.stdout.write(
        "source materials rewritten as whole documents\n"
        f"  parsed_root: {outputs.parsed_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  rewritten_pages: {rewritten_pages}\n"
        f"  failed_pages: {failed_pages}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"(rewritten: {output.rewritten_page_count}, failed: {output.failed_page_count})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed_pages == 0 and outputs.source_outputs else 1


def rewrite_vocabulary_guided_source_material_cli(
    *,
    parsed_bundle_dir: Path,
    output_root: Path,
    dictionary_path: Path,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_vocabulary_guided_parsed_source_material(
                parsed_bundle_dir,
                dictionary_path=dictionary_path,
                output_root=output_root,
                client=client,
                model=model,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "source material rewritten with vocabulary guidance\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  dictionary: {dictionary_path}\n"
        f"  selected_vocabulary: {len(outputs.selected_vocabulary)}\n"
        f"  rewritten_pages: {outputs.rewritten_page_count}\n"
        f"  failed_pages: {outputs.failed_page_count}\n"
        f"  content: {outputs.output_dir / 'content.md'}\n"
    )
    sys.stdout.flush()
    return 0 if outputs.failed_page_count == 0 else 1


def rewrite_vocabulary_guided_source_materials_cli(
    *,
    parsed_root: Path,
    output_root: Path,
    dictionary_path: Path,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_vocabulary_guided_parsed_source_materials(
                parsed_root,
                dictionary_path=dictionary_path,
                output_root=output_root,
                client=client,
                model=model,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
    rewritten_pages = sum(output.rewritten_page_count for output in outputs.source_outputs)
    selected_vocabulary_count = sum(len(output.selected_vocabulary) for output in outputs.source_outputs)
    sys.stdout.write(
        "source materials rewritten with vocabulary guidance\n"
        f"  parsed_root: {outputs.parsed_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  dictionary: {dictionary_path}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  selected_vocabulary: {selected_vocabulary_count}\n"
        f"  rewritten_pages: {rewritten_pages}\n"
        f"  failed_pages: {failed_pages}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"(selected: {len(output.selected_vocabulary)}, rewritten: {output.rewritten_page_count}, "
            f"failed: {output.failed_page_count})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed_pages == 0 and outputs.source_outputs else 1


def print_class_material_cli(
    *,
    rewritten_bundle_dir: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=600.0,
        max_output_tokens=16384,
    )
    try:
        try:
            outputs = print_class_material(
                rewritten_bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                font_path=font_path,
                body_font_size=body_font_size,
                progress=emit_printable_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (PrintableMaterialError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "printable class material written\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  status: {'written' if outputs.accepted else 'failed'}\n"
    )
    if outputs.accepted:
        sys.stdout.write(
            f"  docx: {outputs.output_dir / 'material.docx'}\n"
        )
    else:
        sys.stdout.write(f"  failure: {outputs.failure_reason}\n")
    sys.stdout.flush()
    return 0 if outputs.accepted else 1


def print_class_materials_cli(
    *,
    rewritten_root: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=600.0,
        max_output_tokens=16384,
    )
    try:
        try:
            outputs = print_class_materials(
                rewritten_root,
                output_root=output_root,
                client=client,
                model=model,
                font_path=font_path,
                body_font_size=body_font_size,
                progress=emit_printable_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (PrintableMaterialError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed = sum(1 for output in outputs.source_outputs if not output.accepted)
    written = sum(1 for output in outputs.source_outputs if output.accepted)
    sys.stdout.write(
        "printable class materials written\n"
        f"  rewritten_root: {outputs.rewritten_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  written: {written}\n"
        f"  failed: {failed}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"({'written' if output.accepted else 'failed'})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed == 0 and written > 0 else 1


def emit_source_rewrite_progress(source_id: str, detail: str) -> None:
    sys.stdout.write(f"rewrite source [{source_id}] {detail}\n")
    sys.stdout.flush()


def emit_printable_progress(source_id: str, detail: str) -> None:
    sys.stdout.write(f"print class [{source_id}] {detail}\n")
    sys.stdout.flush()


def warn_if_model_unload_fails(client: SupportsCompletion) -> None:
    try:
        unload_model_if_supported(client)
    except Exception as error:  # pragma: no cover - defensive cleanup path
        sys.stderr.write(f"wordbyword: warning: could not unload Ollama model: {error}\n")
        sys.stderr.flush()


def paddleocr_vl_settings_from_args(args: argparse.Namespace) -> PaddleOCRVLSettings | None:
    if getattr(args, "pdf_parser", "heuristic") != "paddleocr-vl":
        return None
    return PaddleOCRVLSettings(
        device=args.paddleocr_vl_device,
        engine=args.paddleocr_vl_engine,
        vl_rec_backend=args.paddleocr_vl_rec_backend,
        vl_rec_server_url=args.paddleocr_vl_rec_server_url,
        vl_rec_api_model_name=args.paddleocr_vl_rec_api_model_name,
        vl_rec_api_key=args.paddleocr_vl_rec_api_key,
        use_doc_orientation_classify=args.paddleocr_vl_doc_orientation or None,
        use_doc_unwarping=args.paddleocr_vl_doc_unwarping or None,
        use_layout_detection=False if args.paddleocr_vl_no_layout_detection else None,
        use_chart_recognition=args.paddleocr_vl_chart_recognition or None,
        use_seal_recognition=args.paddleocr_vl_seal_recognition or None,
        use_ocr_for_image_block=False if args.paddleocr_vl_no_ocr_for_image_block else None,
        max_new_tokens=args.paddleocr_vl_max_new_tokens,
        sidecar_env=args.paddleocr_vl_sidecar_env.as_posix()
        if args.paddleocr_vl_sidecar_env is not None
        else None,
        sidecar_python=args.paddleocr_vl_sidecar_python.as_posix()
        if args.paddleocr_vl_sidecar_python is not None
        else None,
        sidecar_timeout_seconds=args.paddleocr_vl_sidecar_timeout_seconds,
    )


def prompt_generated_example_retry(
    *,
    summary: GeneratedExampleAttemptSummary,
    output_dir: Path,
    model: str,
) -> bool:
    if summary.failed_count <= 0 or not sys.stdin.isatty():
        return False

    attempts_path = output_dir / "13_generated_example_attempts.jsonl"
    failures_path = output_dir / "13_generated_example_failures.jsonl"
    sys.stdout.write(
        "\n"
        f"wordbyword: candidate-example attempt {summary.attempt_number} with {model} left "
        f"{summary.failed_count:,} failed row(s).\n"
        f"  attempts: {attempts_path}\n"
        f"  failures: {failures_path}\n"
    )
    sys.stdout.flush()

    while True:
        try:
            response = input("Retry the failed subset with Ollama? [Y/n] ").strip().lower()
        except EOFError:
            return False
        if response in {"", "y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        sys.stdout.write("Please answer Y or n.\n")
        sys.stdout.flush()


def link_cleaned_examples(*, output_dir: Path) -> int:
    try:
        outputs = write_linked_dictionary_candidates_from_saved(output_dir, require_inputs=True)
    except ValueError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    assert outputs is not None
    refresh_saved_output_metadata(output_dir, linked_dictionary_outputs=outputs)

    emit_rendered_artifact_drop(
        "12_dictionary_candidates_with_cleaned_examples",
        file_label=str(output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl"),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("replaced", f"{outputs.linked_count:,} examples"),
            ("rewrite", f"{outputs.unlinked_count:,} pending"),
            ("status", "replay // no llm calls"),
        ),
    )
    return 0


def add_reference_meanings(*, output_dir: Path, reference_dictionary_path: Path) -> int:
    try:
        outputs = write_meaning_dictionary_candidates_from_saved(
            output_dir,
            reference_dictionary_path=reference_dictionary_path,
        )
    except ValueError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_rendered_artifact_drop(
        "14_vocabulary_with_meanings",
        file_label=str(output_dir / "14_vocabulary_with_meanings.jsonl"),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("matched", f"{outputs.matched_count:,} meanings"),
            ("missing", f"{outputs.unmatched_count:,} meanings"),
            ("status", "replay // no llm calls"),
        ),
    )
    return 0


def generate_missing_meanings(
    *,
    output_dir: Path,
    model: str,
    base_url: str | None = None,
    generated_meaning_client: SupportsCompletion | None = None,
) -> int:
    owns_client = generated_meaning_client is None
    client = generated_meaning_client or ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=300.0,
        max_output_tokens=4096,
    )

    def generated_meaning_progress(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=model,
            label="meaning",
            item_label="terms",
        )

    try:
        try:
            outputs = write_generated_meaning_dictionary_candidates_from_saved(
                output_dir,
                generated_meaning_client=client,
                generated_meaning_progress=generated_meaning_progress,
            )
        finally:
            if owns_client:
                warn_if_model_unload_fails(client)
    except (ValueError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_rendered_artifact_drop(
        "15_final_dictionary",
        file_label=str(output_dir / "15_final_dictionary.jsonl"),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("generated", f"{outputs.generated_count:,} meanings"),
            ("preserved", f"{outputs.preserved_count:,} meanings"),
            ("missing", f"{outputs.missing_count:,} meanings"),
            ("model", model),
        ),
    )
    return 0
