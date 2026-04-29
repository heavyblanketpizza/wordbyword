from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from wordbyword.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupBatchProgress
from wordbyword.example_generation import GeneratedExampleAttemptSummary
from wordbyword.llm import LLMRequestError, LLMResponseError, SupportsCompletion, ollama_client
from wordbyword.pipeline import PipelineUsageError, run_pipeline
from wordbyword.terminal_ui import (
    emit_artifact_drop,
    emit_boot_screen,
    emit_cleanup_progress,
    emit_completion_screen,
    emit_stage_update,
    render_artifact_drop,
)
from wordbyword.writers import refresh_saved_output_metadata, write_linked_dictionary_candidates_from_saved
from wordbyword.saved_outputs import (
    write_generated_meaning_dictionary_candidates_from_saved,
    write_meaning_dictionary_candidates_from_saved,
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
        default=Path("build-dictionary"),
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
            "references/reference-dictionary.jsonl to write "
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
        default=Path("references/reference-dictionary.jsonl"),
        help=(
            "Reference JSONL with canonical_key and meaning fields for --add-reference-meanings. "
            "Defaults to references/reference-dictionary.jsonl."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
    if args.retry_generated_examples and (
        args.clean_example_sentences or args.clean_dictionary_candidates or args.generate_missing_examples
    ):
        parser.exit(
            2,
            "wordbyword: error: --retry-generated-examples cannot be combined with "
            "--clean-example-sentences, --clean-dictionary-candidates, or --generate-missing-examples.\n",
        )

    emit_boot_screen(
        input_dir=args.input,
        output_dir=args.output,
        clean_example_sentences=(
            args.clean_example_sentences
            or args.clean_dictionary_candidates
            or args.generate_missing_examples
            or args.retry_generated_examples
        ),
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

    def generated_example_progress(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=args.ollama_model,
            label="candidate example",
            item_label="terms",
        )

    def generated_example_retry_decider(summary: GeneratedExampleAttemptSummary) -> bool:
        if args.retry_generated_examples:
            return False
        return prompt_generated_example_retry(
            summary=summary,
            output_dir=args.output,
            model=args.ollama_model,
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
            return
        if stage == "12_dictionary_candidates_with_cleaned_examples":
            replaced_count = sum(
                1 for entry in entries
                if bool(entry.get("example_replaced_with_cleaned_sentence"))
            )
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
            generated_count = sum(
                1 for entry in entries
                if bool(entry.get("example_generated_with_ollama"))
            )
            emit_artifact_drop(
                stage,
                file_label="13_vocabulary_with_generated_examples.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("generated", f"{generated_count:,} examples"),
                    ("preserved", f"{len(entries) - generated_count:,} existing"),
                    ("model", args.ollama_model),
                ),
            )
            return
        if stage == "14_vocabulary_with_meanings":
            matched_count = sum(
                1 for entry in entries
                if bool(str(entry.get("meaning", "")).strip())
            )
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
            generated_count = sum(
                1 for entry in entries
                if bool(entry.get("meaning_generated_with_ollama"))
            )
            emit_artifact_drop(
                stage,
                file_label="15_final_dictionary.jsonl",
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("generated", f"{generated_count:,} meanings"),
                    ("preserved", f"{len(entries) - generated_count:,} meanings"),
                    ("model", args.ollama_model),
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
            generate_missing_examples=args.generate_missing_examples,
            retry_generated_examples=args.retry_generated_examples,
            sentence_cleanup_model=args.ollama_model,
            sentence_cleanup_base_url=args.ollama_base_url,
            progress=emit_stage_update,
            cleanup_progress=example_cleanup_progress,
            dictionary_cleanup_progress=dictionary_cleanup_progress,
            generated_example_progress=generated_example_progress,
            generated_example_retry_decider=generated_example_retry_decider,
            artifact_callback=artifact_callback,
        )
    except PipelineUsageError as exc:
        parser.exit(2, f"wordbyword: error: {exc}\n")

    emit_completion_screen(
        result,
        input_dir=args.input,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=(
            args.clean_example_sentences
            or args.clean_dictionary_candidates
            or args.generate_missing_examples
            or args.retry_generated_examples
        ),
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

    import os
    import shutil

    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    width = shutil.get_terminal_size(fallback=(100, 24)).columns
    rendered = render_artifact_drop(
        "12_dictionary_candidates_with_cleaned_examples",
        file_label=str(output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl"),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("replaced", f"{outputs.linked_count:,} examples"),
            ("rewrite", f"{outputs.unlinked_count:,} pending"),
            ("status", "replay // no llm calls"),
        ),
        color=color,
        width=width,
    )
    sys.stdout.write(rendered)
    sys.stdout.write("\n")
    sys.stdout.flush()
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

    import os
    import shutil

    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    width = shutil.get_terminal_size(fallback=(100, 24)).columns
    rendered = render_artifact_drop(
        "14_vocabulary_with_meanings",
        file_label=str(output_dir / "14_vocabulary_with_meanings.jsonl"),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("matched", f"{outputs.matched_count:,} meanings"),
            ("missing", f"{outputs.unmatched_count:,} meanings"),
            ("status", "replay // no llm calls"),
        ),
        color=color,
        width=width,
    )
    sys.stdout.write(rendered)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def generate_missing_meanings(
    *,
    output_dir: Path,
    model: str,
    base_url: str | None = None,
    generated_meaning_client: SupportsCompletion | None = None,
) -> int:
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
        outputs = write_generated_meaning_dictionary_candidates_from_saved(
            output_dir,
            generated_meaning_client=client,
            generated_meaning_progress=generated_meaning_progress,
        )
    except (ValueError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    import os
    import shutil

    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    width = shutil.get_terminal_size(fallback=(100, 24)).columns
    rendered = render_artifact_drop(
        "15_final_dictionary",
        file_label=str(output_dir / "15_final_dictionary.jsonl"),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("generated", f"{outputs.generated_count:,} meanings"),
            ("preserved", f"{outputs.preserved_count:,} meanings"),
            ("missing", f"{outputs.missing_count:,} meanings"),
            ("model", model),
        ),
        color=color,
        width=width,
    )
    sys.stdout.write(rendered)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0
