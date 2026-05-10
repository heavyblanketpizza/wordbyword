"""Command-line entry points."""

from wordbyword.cli.app import (
    add_reference_meanings,
    build_usage_signals_cli,
    generate_missing_meanings,
    link_cleaned_examples,
    load_dictionary_candidates,
    main,
    paddleocr_vl_settings_from_args,
    parse_pdf_parser_name,
    pipeline_cleanup_requested,
    prompt_generated_example_retry,
    rename_class_session_archive_cli,
    resolve_distribution_path,
    show_distribution,
    source_parse_options_from_args,
)
from wordbyword.cli.options import (
    DEFAULT_INPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REFERENCE_DICTIONARY_PATH,
    SourceParseOptions,
    build_parser,
    validate_args,
)

__all__ = [
    "DEFAULT_INPUT_DIR",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_REFERENCE_DICTIONARY_PATH",
    "SourceParseOptions",
    "add_reference_meanings",
    "build_usage_signals_cli",
    "build_parser",
    "generate_missing_meanings",
    "link_cleaned_examples",
    "load_dictionary_candidates",
    "main",
    "paddleocr_vl_settings_from_args",
    "parse_pdf_parser_name",
    "pipeline_cleanup_requested",
    "prompt_generated_example_retry",
    "rename_class_session_archive_cli",
    "resolve_distribution_path",
    "show_distribution",
    "source_parse_options_from_args",
    "validate_args",
]
