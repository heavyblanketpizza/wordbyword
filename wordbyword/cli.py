from __future__ import annotations

import argparse
from pathlib import Path

from wordbyword.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupBatchProgress
from wordbyword.pipeline import run_pipeline
from wordbyword.terminal_ui import (
    emit_boot_screen,
    emit_cleanup_progress,
    emit_completion_screen,
    emit_stage_update,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wordbyword",
        description="Extract Korean vocabulary candidates from PDFs and text documents.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("original"),
        help="Folder containing source PDFs, .txt, or .md files. Defaults to ./original.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output"),
        help="Folder where CSV and JSON outputs are written. Defaults to ./output.",
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
        help="Run the optional Ollama cleanup stage and write 10_cleaned_example_sentences.csv.",
    )
    parser.add_argument(
        "--ollama-model",
        default=DEFAULT_EXAMPLE_CLEANUP_MODEL,
        help=(
            "Ollama model used for --clean-example-sentences. "
            f"Defaults to {DEFAULT_EXAMPLE_CLEANUP_MODEL}."
        ),
    )
    parser.add_argument(
        "--ollama-base-url",
        default=None,
        help="Optional Ollama base URL override. Defaults to WORDBYWORD_OLLAMA_BASE_URL or http://localhost:11434.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    emit_boot_screen(
        input_dir=args.input,
        output_dir=args.output,
        clean_example_sentences=args.clean_example_sentences,
        sentence_cleanup_model=args.ollama_model,
    )

    def cleanup_progress(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(progress, model=args.ollama_model)

    result = run_pipeline(
        input_dir=args.input,
        output_dir=args.output,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=args.clean_example_sentences,
        sentence_cleanup_model=args.ollama_model,
        sentence_cleanup_base_url=args.ollama_base_url,
        progress=emit_stage_update,
        cleanup_progress=cleanup_progress,
    )

    emit_completion_screen(
        result,
        input_dir=args.input,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=args.clean_example_sentences,
        sentence_cleanup_model=args.ollama_model,
    )
    return 0
