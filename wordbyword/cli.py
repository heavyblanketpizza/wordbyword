from __future__ import annotations

import argparse
from pathlib import Path

from wordbyword.pipeline import run_pipeline


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    result = run_pipeline(
        input_dir=args.input,
        output_dir=args.output,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
    )

    print(f"Documents scanned: {result.documents}")
    print(f"Page/text units extracted: {result.extracted_pages}")
    print(f"Sentences parsed: {result.sentences}")
    print(f"Token occurrences: {result.token_occurrences}")
    print(f"Unique vocabulary terms: {result.vocabulary_terms}")
    print(f"Unique surface words: {result.surface_words}")
    print(f"Output written to: {result.output_dir}")
    return 0
