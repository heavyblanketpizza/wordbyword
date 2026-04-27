from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from wordbyword.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupBatchProgress
from wordbyword.pipeline import PipelineUsageError, run_pipeline
from wordbyword.terminal_ui import (
    emit_artifact_drop,
    emit_boot_screen,
    emit_cleanup_progress,
    emit_completion_screen,
    emit_stage_update,
    render_artifact_drop,
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
        help="Use saved output/09_example_sentences.csv and output/06_candidate_terms.csv to write 10_cleaned_example_sentences.csv.",
    )
    parser.add_argument(
        "--clean-dictionary-candidates",
        action="store_true",
        help="Use saved output/08_dictionary_candidates.jsonl and output/06_candidate_terms.csv to write 11_cleaned_dictionary_candidates.jsonl.",
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
            "defaults to <output>/11_cleaned_dictionary_candidates.jsonl."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.show_distribution is not None:
        return show_distribution(
            jsonl_path=resolve_distribution_path(args.show_distribution, args.output),
            model=args.ollama_model,
        )

    emit_boot_screen(
        input_dir=args.input,
        output_dir=args.output,
        clean_example_sentences=args.clean_example_sentences or args.clean_dictionary_candidates,
        sentence_cleanup_model=args.ollama_model,
    )

    def example_cleanup_progress(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=args.ollama_model,
            label="example",
            item_label="rows",
        )

    def dictionary_cleanup_progress(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=args.ollama_model,
            label="dictionary",
            item_label="terms",
        )

    def artifact_callback(stage: str, entries: Sequence[Mapping[str, object]]) -> None:
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
                    ("model", args.ollama_model),
                    ("status", "cache locked"),
                ),
            )

    try:
        result = run_pipeline(
            input_dir=args.input,
            output_dir=args.output,
            include_punctuation=args.include_punctuation,
            include_numbers=args.include_numbers,
            clean_example_sentences=args.clean_example_sentences,
            clean_dictionary_candidates=args.clean_dictionary_candidates,
            sentence_cleanup_model=args.ollama_model,
            sentence_cleanup_base_url=args.ollama_base_url,
            progress=emit_stage_update,
            cleanup_progress=example_cleanup_progress,
            dictionary_cleanup_progress=dictionary_cleanup_progress,
            artifact_callback=artifact_callback,
        )
    except PipelineUsageError as exc:
        parser.exit(2, f"wordbyword: error: {exc}\n")

    emit_completion_screen(
        result,
        input_dir=args.input,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=args.clean_example_sentences or args.clean_dictionary_candidates,
        sentence_cleanup_model=args.ollama_model,
    )
    return 0


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

    import os
    import shutil

    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    width = shutil.get_terminal_size(fallback=(100, 24)).columns
    rendered = render_artifact_drop(
        "11_cleaned_dictionary_candidates",
        entries=entries,
        file_label=str(jsonl_path),
        summary=(
            ("relics", f"{len(entries):,} headwords"),
            ("model", model),
            ("status", "replay // no llm calls"),
        ),
        color=color,
        width=width,
    )
    sys.stdout.write(rendered)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0
