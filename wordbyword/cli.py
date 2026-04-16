from __future__ import annotations

import argparse
from pathlib import Path

from wordbyword.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wordbyword",
        description="Extract Korean vocabulary and expression candidates from PDFs and text documents.",
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
        help="Include punctuation and symbol tokens in tokens.csv.",
    )
    parser.add_argument(
        "--include-numbers",
        action="store_true",
        help="Include pure number terms in vocabulary, surface word, and expression outputs.",
    )
    parser.add_argument(
        "--min-expression-size",
        type=int,
        default=2,
        help="Smallest surface n-gram size to save as an expression candidate.",
    )
    parser.add_argument(
        "--max-expression-size",
        type=int,
        default=5,
        help="Largest surface n-gram size to save as an expression candidate.",
    )
    parser.add_argument(
        "--min-expression-count",
        type=int,
        default=1,
        help="Minimum frequency for expression candidates written to expressions.csv.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.min_expression_size < 2:
        parser.error("--min-expression-size must be at least 2")
    if args.max_expression_size < args.min_expression_size:
        parser.error("--max-expression-size must be greater than or equal to --min-expression-size")
    if args.min_expression_count < 1:
        parser.error("--min-expression-count must be at least 1")

    result = run_pipeline(
        input_dir=args.input,
        output_dir=args.output,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        min_expression_size=args.min_expression_size,
        max_expression_size=args.max_expression_size,
        min_expression_count=args.min_expression_count,
    )

    print(f"Documents scanned: {result.documents}")
    print(f"Token occurrences: {result.token_occurrences}")
    print(f"Unique vocabulary terms: {result.vocabulary_terms}")
    print(f"Unique surface words: {result.surface_words}")
    print(f"Expression candidates: {result.expression_candidates}")
    print(f"Output written to: {result.output_dir}")
    return 0
