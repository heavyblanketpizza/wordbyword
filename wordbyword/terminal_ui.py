from __future__ import annotations

import math
import os
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from wordbyword.example_cleanup import CleanupBatchProgress
    from wordbyword.pipeline import PipelineResult
    from wordbyword.source_materials import AgenticOllamaProgress

TOKYO_NIGHT = {
    "bg": "1a1b26",
    "fg": "c0caf5",
    "comment": "565f89",
    "blue": "7aa2f7",
    "cyan": "7dcfff",
    "green": "9ece6a",
    "orange": "ff9e64",
    "pink": "f7768e",
    "purple": "bb9af7",
    "yellow": "e0af68",
}

PHASE_COLORS = {
    "scan": TOKYO_NIGHT["blue"],
    "extract": TOKYO_NIGHT["cyan"],
    "analyze": TOKYO_NIGHT["purple"],
    "cleanup": TOKYO_NIGHT["green"],
    "write": TOKYO_NIGHT["orange"],
    "complete": TOKYO_NIGHT["pink"],
}

ASCII_ART = [
    "╔═══════════════════════════════════════════════╗",
    "║  ░▒▓█  NIGHT CITY SIGNAL // RELIC LINK  █▓▒░  ║",
    "╠═══════════════════════════════════════════════╣",
    "║                       ▓▓                      ║",
    "║          ▓▓         ██████        ▓▓          ║",
    "║    ▓▓   ████      ██████████    ████   ▓▓     ║",
    "║   ████ ██████▓▓  ████████████  ██████ ████    ║",
    "║   ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀   ║",
    "║                                               ║",
    "║   ╭───────╮  ░▒▓ A-SIDE // 90 ▓▒░  ╭───────╮  ║",
    "║   │ ◉ ▣ ◉ │  ▓▒░░░▒▒▓▓▒▒▒░░░▒▓     │ ◉ ▣ ◉ │  ║",
    "║   ╰───────╯  ╶─╴╶─╴╶─╴╶─╴╶─╴╶─╴    ╰───────╯  ║",
    "║                                               ║",
    "║      ░▒▓█  RELIC LINK // ACTIVE  █▓▒░         ║",
    "╚═══════════════════════════════════════════════╝",
]

ASCII_ART_PALETTE = [
    TOKYO_NIGHT["purple"],
    TOKYO_NIGHT["cyan"],
    TOKYO_NIGHT["purple"],
    TOKYO_NIGHT["blue"],
    TOKYO_NIGHT["pink"],
    TOKYO_NIGHT["pink"],
    TOKYO_NIGHT["orange"],
    TOKYO_NIGHT["yellow"],
    TOKYO_NIGHT["comment"],
    TOKYO_NIGHT["green"],
    TOKYO_NIGHT["cyan"],
    TOKYO_NIGHT["green"],
    TOKYO_NIGHT["comment"],
    TOKYO_NIGHT["pink"],
    TOKYO_NIGHT["purple"],
]

BOOT_GLYPH = [
    "  ▄▄▄▄ ▄▄▄▄ ▄▄▄▄    ░▒▓█▓▒░    ▄▄▄▄ ▄▄▄▄ ▄▄▄▄  ",
    "  █▄▄█ █▄▄█ █▄▄█  ░▒▓ wbw ▓▒░  █▄▄█ █▄▄█ █▄▄█  ",
    "  ────────────────────────────────────────────  ",
]

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def emit_boot_screen(
    *,
    input_dir: Path,
    output_dir: Path,
    clean_example_sentences: bool,
    sentence_cleanup_model: str,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_boot_screen(
            input_dir=input_dir,
            output_dir=output_dir,
            clean_example_sentences=clean_example_sentences,
            sentence_cleanup_model=sentence_cleanup_model,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()


def emit_source_material_boot_screen(
    *,
    source_path: Path,
    output_root: Path,
    batch: bool,
    parse_pdf: bool,
    agentic_ollama_parse: bool,
    agentic_ollama_model: str,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_source_material_boot_screen(
            source_path=source_path,
            output_root=output_root,
            batch=batch,
            parse_pdf=parse_pdf,
            agentic_ollama_parse=agentic_ollama_parse,
            agentic_ollama_model=agentic_ollama_model,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()


def emit_stage_update(phase: str, detail: str, stream: TextIO | None = None) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(render_stage_update(phase, detail, color=_supports_color(stream)))
    stream.write("\n")
    stream.flush()


def emit_cleanup_progress(
    progress: "CleanupBatchProgress",
    *,
    model: str | None = None,
    label: str = "cleanup",
    item_label: str = "rows",
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    color = _supports_color(stream)
    width = _terminal_width()
    line = render_cleanup_progress(
        progress,
        model=model,
        label=label,
        item_label=item_label,
        color=color,
        width=width,
    )
    stream.write("\033[2K\r")
    stream.write(line)
    if progress.phase == "complete" and progress.completed_batches >= progress.total_batches:
        stream.write("\n")
    stream.flush()


def emit_agentic_ollama_progress(
    progress: "AgenticOllamaProgress",
    *,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    line = render_agentic_ollama_progress(
        progress,
        color=_supports_color(stream),
        width=_terminal_width(),
    )
    stream.write("\033[2K\r")
    stream.write(line)
    if progress.phase == "complete":
        stream.write("\n")
    stream.flush()


def emit_completion_screen(
    result: "PipelineResult",
    *,
    input_dir: Path,
    include_punctuation: bool,
    include_numbers: bool,
    clean_example_sentences: bool,
    sentence_cleanup_model: str,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    interactive = _is_interactive(stream)
    color = _supports_color(stream)
    if interactive:
        stream.write("\n")
    stream.write(
        render_completion_screen(
            result,
            input_dir=input_dir,
            include_punctuation=include_punctuation,
            include_numbers=include_numbers,
            clean_example_sentences=clean_example_sentences,
            sentence_cleanup_model=sentence_cleanup_model,
            color=color,
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    if interactive and not os.environ.get("CI"):
        stream.write("\a")
    stream.flush()


def render_boot_screen(
    *,
    input_dir: Path,
    output_dir: Path,
    clean_example_sentences: bool,
    sentence_cleanup_model: str,
    color: bool,
    width: int = 100,
) -> str:
    mode = "ollama cleanup online" if clean_example_sentences else "deterministic pipeline only"
    if clean_example_sentences:
        mode = f"{mode} // {sentence_cleanup_model}"

    banner = _paint(
        " WORD BY WORD // TOKYO NIGHT RELIC BOOT ",
        fg=TOKYO_NIGHT["bg"],
        bg=TOKYO_NIGHT["cyan"],
        bold=True,
        color=color,
    )
    glyph_palette = (TOKYO_NIGHT["pink"], TOKYO_NIGHT["purple"], TOKYO_NIGHT["cyan"])
    glyph = [
        _paint(line, fg=glyph_palette[index], bold=index == 1, color=color)
        for index, line in enumerate(BOOT_GLYPH)
    ]
    underline = _paint("=" * min(width, 72), fg=TOKYO_NIGHT["comment"], color=color)
    steps = _paint(
        "▶ scan ─► extract ─► analyze ─► write ─► relic",
        fg=TOKYO_NIGHT["purple"],
        bold=True,
        color=color,
    )
    details = [
        f"input   :: {input_dir}",
        f"output  :: {output_dir}",
        f"mode    :: {mode}",
    ]
    lines = [*glyph, banner, underline, steps]
    lines.extend(_paint(line, fg=TOKYO_NIGHT["fg"], color=color) for line in details)
    return "\n".join(lines)


def render_source_material_boot_screen(
    *,
    source_path: Path,
    output_root: Path,
    batch: bool,
    parse_pdf: bool,
    agentic_ollama_parse: bool,
    agentic_ollama_model: str,
    color: bool,
    width: int = 100,
) -> str:
    source_label = "source_dir" if batch else "source"
    mode_parts = ["heuristic pdf" if parse_pdf else "workspace only"]
    if agentic_ollama_parse:
        mode_parts.append(f"agentic ollama // {agentic_ollama_model}")
    else:
        mode_parts.append("agentic off")
    banner = _paint(
        " SOURCE MATERIAL // TOKYO NIGHT PARSE BOOT ",
        fg=TOKYO_NIGHT["bg"],
        bg=TOKYO_NIGHT["purple"],
        bold=True,
        color=color,
    )
    glyph_palette = (TOKYO_NIGHT["cyan"], TOKYO_NIGHT["purple"], TOKYO_NIGHT["pink"])
    glyph = [
        _paint(line, fg=glyph_palette[index], bold=index == 1, color=color)
        for index, line in enumerate(BOOT_GLYPH)
    ]
    underline = _paint("=" * min(width, 72), fg=TOKYO_NIGHT["comment"], color=color)
    steps = _paint(
        "▶ prepare ─► render pages ─► ollama vision ─► write bundle",
        fg=TOKYO_NIGHT["cyan"],
        bold=True,
        color=color,
    )
    details = [
        f"{source_label:<10} :: {source_path}",
        f"output    :: {output_root}",
        f"mode      :: {' + '.join(mode_parts)}",
    ]
    lines = [*glyph, banner, underline, steps]
    lines.extend(_paint(line, fg=TOKYO_NIGHT["fg"], color=color) for line in details)
    return "\n".join(lines)


def render_stage_update(phase: str, detail: str, *, color: bool) -> str:
    phase_color = PHASE_COLORS.get(phase, TOKYO_NIGHT["blue"])
    tag = _paint(f" {phase.upper():<8} ", fg=TOKYO_NIGHT["bg"], bg=phase_color, bold=True, color=color)
    arrow = _paint("▶", fg=phase_color, bold=True, color=color)
    message = _paint(detail, fg=TOKYO_NIGHT["fg"], color=color)
    return f"{tag} {arrow} {message}"


def render_cleanup_progress(
    progress: "CleanupBatchProgress",
    *,
    model: str | None = None,
    label: str = "cleanup",
    item_label: str = "rows",
    color: bool,
    width: int = 100,
) -> str:
    phase_color = PHASE_COLORS["cleanup"]
    tag_text = f" {label.strip().upper()[:10]:<10} "
    tag = _paint(
        tag_text,
        fg=TOKYO_NIGHT["bg"],
        bg=phase_color,
        bold=True,
        color=color,
    )
    arrow_glyph = "▶" if progress.phase == "start" else "▣"
    arrow = _paint(arrow_glyph, fg=phase_color, bold=True, color=color)

    total_rows = max(progress.total_rows, 1)
    ratio = max(0.0, min(1.0, progress.completed_rows / total_rows))

    suffix_plain = f" :: {model}" if model else ""
    head_plain = (
        f"batch {progress.batch_index:02d}/{max(progress.total_batches, 1):02d}  "
        f"{item_label} {progress.completed_rows}/{progress.total_rows}  "
    )
    tail_plain = f"  {ratio * 100:5.1f}%{suffix_plain}"
    overhead = len(tag_text) + 1 + 1 + 1 + len(head_plain) + len(tail_plain) + 2
    bar_width = max(8, min(28, width - overhead))
    filled = int(round(bar_width * ratio))
    filled = max(0, min(bar_width, filled))
    bar_filled = _paint("█" * filled, fg=phase_color, bold=True, color=color)
    bar_empty = _paint("░" * (bar_width - filled), fg=TOKYO_NIGHT["comment"], color=color)
    bracket_open = _paint("[", fg=TOKYO_NIGHT["comment"], color=color)
    bracket_close = _paint("]", fg=TOKYO_NIGHT["comment"], color=color)

    head_text = _paint(head_plain, fg=TOKYO_NIGHT["fg"], color=color)
    pct_text = _paint(f"{ratio * 100:5.1f}%", fg=TOKYO_NIGHT["yellow"], bold=True, color=color)
    suffix_text = _paint(suffix_plain, fg=TOKYO_NIGHT["comment"], color=color) if suffix_plain else ""

    return (
        f"{tag} {arrow} {head_text}{bracket_open}{bar_filled}{bar_empty}{bracket_close}  "
        f"{pct_text}{suffix_text}"
    )


def render_agentic_ollama_progress(
    progress: "AgenticOllamaProgress",
    *,
    color: bool,
    width: int = 100,
) -> str:
    phase_color = TOKYO_NIGHT["purple"] if progress.failed_pages == 0 else TOKYO_NIGHT["orange"]
    tag_text = " AGENTIC   "
    tag = _paint(
        tag_text,
        fg=TOKYO_NIGHT["bg"],
        bg=phase_color,
        bold=True,
        color=color,
    )
    arrow_glyph = "▶" if progress.phase == "start" else "▣"
    arrow = _paint(arrow_glyph, fg=phase_color, bold=True, color=color)

    attempted_pages = max(progress.attempted_pages, 1)
    ratio = max(0.0, min(1.0, progress.completed_pages / attempted_pages))
    page_label = "--" if progress.page_number <= 0 else f"{progress.page_number:02d}"
    skipped_plain = f" skipped {progress.skipped_pages}" if progress.skipped_pages else ""
    head_plain = (
        f"{progress.source_id}  page {page_label}/{progress.document_pages:02d}  "
        f"done {progress.completed_pages}/{progress.attempted_pages}  "
        f"parsed {progress.parsed_pages} failed {progress.failed_pages}{skipped_plain}  "
    )
    tail_plain = f"  {ratio * 100:5.1f}% :: {progress.model}"
    overhead = len(tag_text) + 1 + 1 + 1 + len(head_plain) + len(tail_plain) + 2
    bar_width = max(8, min(28, width - overhead))
    filled = int(round(bar_width * ratio))
    filled = max(0, min(bar_width, filled))
    bar_filled = _paint("█" * filled, fg=phase_color, bold=True, color=color)
    bar_empty = _paint("░" * (bar_width - filled), fg=TOKYO_NIGHT["comment"], color=color)
    bracket_open = _paint("[", fg=TOKYO_NIGHT["comment"], color=color)
    bracket_close = _paint("]", fg=TOKYO_NIGHT["comment"], color=color)

    head_text = _paint(head_plain, fg=TOKYO_NIGHT["fg"], color=color)
    pct_text = _paint(f"{ratio * 100:5.1f}%", fg=TOKYO_NIGHT["yellow"], bold=True, color=color)
    suffix_text = _paint(f" :: {progress.model}", fg=TOKYO_NIGHT["comment"], color=color)
    return (
        f"{tag} {arrow} {head_text}{bracket_open}{bar_filled}{bar_empty}{bracket_close}  "
        f"{pct_text}{suffix_text}"
    )


def render_completion_screen(
    result: "PipelineResult",
    *,
    input_dir: Path,
    include_punctuation: bool,
    include_numbers: bool,
    clean_example_sentences: bool,
    sentence_cleanup_model: str,
    color: bool,
    width: int = 100,
) -> str:
    switches = [
        "+punctuation" if include_punctuation else "-punctuation",
        "+numbers" if include_numbers else "-numbers",
    ]
    cleanup_state = sentence_cleanup_model if clean_example_sentences else "off"
    header = "wordbyword@night-city"
    info_lines = [
        _paint(header, fg=TOKYO_NIGHT["blue"], bold=True, color=color),
        _paint("-" * len(header), fg=TOKYO_NIGHT["comment"], color=color),
        _info_line("status", "pipeline complete", color=color, emphasis=True),
        _info_line("input", str(input_dir), color=color),
        _info_line("output", str(result.output_dir), color=color),
        _info_line("documents", _format_number(result.documents), color=color),
        _info_line("text units", _format_number(result.extracted_pages), color=color),
        _info_line("sentences", _format_number(result.sentences), color=color),
        _info_line("tokens", _format_number(result.token_occurrences), color=color),
        _info_line("vocabulary", _format_number(result.vocabulary_terms), color=color),
        _info_line("surface", _format_number(result.surface_words), color=color),
        _info_line("cleanup", cleanup_state, color=color),
        _info_line("switches", " ".join(switches), color=color),
    ]

    painted_art = [
        _paint(line, fg=ASCII_ART_PALETTE[index], bold=index in (1, 13), color=color)
        for index, line in enumerate(ASCII_ART)
    ]
    composed = _compose_columns(painted_art, info_lines, width=width)
    lines = [
        _paint(
            "  RELIC LINK STABLE // OUTPUT STACK WRITTEN  ",
            fg=TOKYO_NIGHT["bg"],
            bg=TOKYO_NIGHT["pink"],
            bold=True,
            color=color,
        ),
        _paint(
            "  ░▒▓█ ▓▒░  signal locked  ░▒▓ ▓▒░  cassette spinning  ░▒▓█",
            fg=TOKYO_NIGHT["comment"],
            color=color,
        ),
        composed,
    ]
    return "\n".join(lines)


HISTOGRAM_FRACTIONS = " ▁▂▃▄▅▆▇█"

RELIC_DROP_ART_QUALITY = [
    "    ░▒▓█▓▒░  ░▒▓ DICTIONARY RELIC // CACHE LOCKED ▓▒░  ░▒▓█▓▒░    ",
    "  ╭──────────────────────────────────────────────────────────╮  ",
    "  │  ▓▒░  ░▒▓ 11_cleaned_dictionary_candidates.jsonl ▓▒░  ░▒▓  │  ",
    "  │   ◉─◉─◉   ▟▙ headword stack online ▟▙   ◉─◉─◉   ░░░░░░░   │  ",
    "  ╰──────────────────────────────────────────────────────────╯  ",
]

RELIC_DROP_ART_SENTENCES = [
    "    ░▒▓█▓▒░  ░▒▓ EXAMPLE STREAM // SIGNAL FIXED ▓▒░  ░▒▓█▓▒░    ",
    "  ╭──────────────────────────────────────────────────────────╮  ",
    "  │   ▓▒░  ░▒▓ 09_example_sentences.csv ▓▒░  ░░▒▒▓▓▒▒░░░░░░   │  ",
    "  │   ◉─◉─◉   ▟▙ sentence stack online ▟▙   ◉─◉─◉   ░░░░░░   │  ",
    "  ╰──────────────────────────────────────────────────────────╯  ",
]

RELIC_DROP_ART_LINKED = [
    "    ░▒▓█▓▒░  ░▒▓ EXAMPLE RELAY // CACHE MERGED ▓▒░  ░▒▓█▓▒░    ",
    "  ╭──────────────────────────────────────────────────────────╮  ",
    "  │  ▓▒░  ░▒▓ 12_dictionary_candidates_with_cleaned_examples ▓▒  │  ",
    "  │   ◉─◉─◉   ▟▙ relay stack online ▟▙   ◉─◉─◉   ░░░░░░░░   │  ",
    "  ╰──────────────────────────────────────────────────────────╯  ",
]

RELIC_DROP_ART_GENERATED = [
    "    ░▒▓█▓▒░  ░▒▓ EXAMPLE FORGE // VOCAB WRITTEN ▓▒░  ░▒▓█▓▒░    ",
    "  ╭──────────────────────────────────────────────────────────────────╮  ",
    "  │  ▓▒░  ░▒▓ 13_vocabulary_with_generated_examples ▓▒░   │  ",
    "  │   ◉─◉─◉   ▟▙ forge stack online ▟▙   ◉─◉─◉   ░░░░░░░░░░░   │  ",
    "  ╰──────────────────────────────────────────────────────────────────╯  ",
]

RELIC_DROP_ART_MEANINGS = [
    "    ░▒▓█▓▒░  ░▒▓ MEANING RELAY // REFERENCE MERGED ▓▒░  ░▒▓█▓▒░    ",
    "  ╭──────────────────────────────────────────────────────────────╮  ",
    "  │  ▓▒░  ░▒▓ 14_vocabulary_with_meanings.jsonl ▓▒░   │  ",
    "  │   ◉─◉─◉   ▟▙ meaning stack online ▟▙   ◉─◉─◉   ░░░░░░░░   │  ",
    "  ╰──────────────────────────────────────────────────────────────╯  ",
]

RELIC_DROP_ART_GENERATED_MEANINGS = [
    "    ░▒▓█▓▒░  ░▒▓ MEANING FORGE // EMPTY SLOTS FILLED ▓▒░  ░▒▓█▓▒░    ",
    "  ╭────────────────────────────────────────────────────────────────╮  ",
    "  │  ▓▒░  ░▒▓ 15_final_dictionary ▓▒░ │  ",
    "  │   ◉─◉─◉   ▟▙ generated meanings online ▟▙   ◉─◉─◉   ░░░░░   │  ",
    "  ╰────────────────────────────────────────────────────────────────╯  ",
]

RELIC_DROP_PALETTE = (
    TOKYO_NIGHT["pink"],
    TOKYO_NIGHT["purple"],
    TOKYO_NIGHT["cyan"],
    TOKYO_NIGHT["yellow"],
    TOKYO_NIGHT["pink"],
)


def emit_artifact_drop(
    stage: str,
    *,
    entries: Sequence[Mapping[str, object]] | None = None,
    file_label: str | None = None,
    summary: Sequence[tuple[str, str]] = (),
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_artifact_drop(
            stage,
            entries=entries,
            file_label=file_label,
            summary=summary,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()


def emit_dictionary_distribution(
    entries: Sequence[Mapping[str, object]],
    *,
    bins: int = 24,
    height: int = 10,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_dictionary_distribution(
            entries,
            color=_supports_color(stream),
            width=_terminal_width(),
            bins=bins,
            height=height,
        )
    )
    stream.write("\n")
    stream.flush()


def render_artifact_drop(
    stage: str,
    *,
    entries: Sequence[Mapping[str, object]] | None = None,
    file_label: str | None = None,
    summary: Sequence[tuple[str, str]] = (),
    color: bool,
    width: int = 100,
) -> str:
    if stage == "11_cleaned_dictionary_candidates":
        art = RELIC_DROP_ART_QUALITY
        accent = TOKYO_NIGHT["pink"]
    elif stage == "12_dictionary_candidates_with_cleaned_examples":
        art = RELIC_DROP_ART_LINKED
        accent = TOKYO_NIGHT["orange"]
    elif stage == "13_vocabulary_with_generated_examples":
        art = RELIC_DROP_ART_GENERATED
        accent = TOKYO_NIGHT["green"]
    elif stage == "14_vocabulary_with_meanings":
        art = RELIC_DROP_ART_MEANINGS
        accent = TOKYO_NIGHT["purple"]
    elif stage == "15_final_dictionary":
        art = RELIC_DROP_ART_GENERATED_MEANINGS
        accent = TOKYO_NIGHT["yellow"]
    else:
        art = RELIC_DROP_ART_SENTENCES
        accent = TOKYO_NIGHT["cyan"]
    label = file_label or _stage_default_label(stage)

    banner = _paint(
        f"  ░▒▓█  RELIC DROP // {label}  █▓▒░  ",
        fg=TOKYO_NIGHT["bg"],
        bg=accent,
        bold=True,
        color=color,
    )
    glyph_lines = [
        _paint(line, fg=RELIC_DROP_PALETTE[index % len(RELIC_DROP_PALETTE)], bold=index in (0, 2), color=color)
        for index, line in enumerate(art)
    ]
    rule = _paint(
        "░▒▓" + "─" * max(0, min(width, 72) - 6) + "▓▒░",
        fg=TOKYO_NIGHT["comment"],
        color=color,
    )

    info_lines: list[str] = []
    for key, value in summary:
        info_lines.append(_info_line(key, value, color=color))

    parts: list[str] = [banner, *glyph_lines, rule]
    if info_lines:
        parts.extend(info_lines)
        parts.append(rule)
    if entries is not None and stage == "11_cleaned_dictionary_candidates":
        parts.append(render_dictionary_distribution(entries, color=color, width=width))
    return "\n".join(parts)


def render_dictionary_distribution(
    entries: Sequence[Mapping[str, object]],
    *,
    color: bool,
    width: int = 100,
    bins: int = 24,
    height: int = 10,
) -> str:
    """Render two cyberpunk Tokyo Night histograms for the dictionary candidates.

    First chart: x-axis quality_score (0.000 .. 1.000), y-axis count of vocab in each bin.
    Second chart: x-axis occurrence count (log-spaced), y-axis count of vocab in each bin.
    """

    safe_bins = max(8, min(bins, max(8, width - 12)))
    safe_height = max(4, height)

    quality_scores = [_safe_float(entry.get("quality_score")) for entry in entries]
    counts = [_safe_int(entry.get("count")) for entry in entries]

    header = _paint(
        "  ░▒▓█  RELIC // DICTIONARY DISTRIBUTION SCAN  █▓▒░  ",
        fg=TOKYO_NIGHT["bg"],
        bg=TOKYO_NIGHT["purple"],
        bold=True,
        color=color,
    )
    rule = _paint(
        "░▒▓" + "─" * max(0, min(width, 72) - 6) + "▓▒░",
        fg=TOKYO_NIGHT["comment"],
        color=color,
    )

    quality_panel = _render_histogram_panel(
        title="quality_score distribution",
        subtitle=_quality_subtitle(quality_scores, bins=safe_bins),
        bin_counts=_quality_score_bin_counts(quality_scores, bins=safe_bins),
        x_axis_labels=("0.00", "0.50", "1.00"),
        height=safe_height,
        color=color,
        bar_color=TOKYO_NIGHT["cyan"],
        accent_color=TOKYO_NIGHT["pink"],
    )
    count_panel = _render_histogram_panel(
        title="occurrence count distribution",
        subtitle=_count_subtitle(counts, bins=safe_bins),
        bin_counts=_log_count_bin_counts(counts, bins=safe_bins),
        x_axis_labels=_log_count_axis_labels(counts),
        height=safe_height,
        color=color,
        bar_color=TOKYO_NIGHT["green"],
        accent_color=TOKYO_NIGHT["purple"],
    )

    return "\n".join([header, rule, quality_panel, "", count_panel, rule])


def _render_histogram_panel(
    *,
    title: str,
    subtitle: str,
    bin_counts: list[int],
    x_axis_labels: tuple[str, str, str],
    height: int,
    color: bool,
    bar_color: str,
    accent_color: str,
) -> str:
    title_line = _paint(f"┃ {title}", fg=accent_color, bold=True, color=color)
    subtitle_line = _paint(f"┃   {subtitle}", fg=TOKYO_NIGHT["comment"], color=color)

    if not bin_counts or all(value == 0 for value in bin_counts):
        empty = _paint("┃   ░▒▓ no signal // empty cache ▓▒░", fg=TOKYO_NIGHT["comment"], color=color)
        return "\n".join([title_line, subtitle_line, empty])

    max_count = max(bin_counts)
    y_label_width = max(3, len(f"{max_count}"))
    bar_count = len(bin_counts)

    rows: list[str] = [title_line, subtitle_line]
    for row_index in range(height, 0, -1):
        threshold_high = max_count * row_index / height
        threshold_low = max_count * (row_index - 1) / height
        chars: list[str] = []
        for value in bin_counts:
            if value >= threshold_high:
                chars.append("█")
            elif value <= threshold_low:
                chars.append(" ")
            else:
                fraction = (value - threshold_low) / max(threshold_high - threshold_low, 1e-9)
                index = max(0, min(8, int(round(fraction * 8))))
                chars.append(HISTOGRAM_FRACTIONS[index])
        bar_segment = _paint("".join(chars), fg=bar_color, color=color)
        if row_index == height:
            label_text = f"{max_count:>{y_label_width}}"
        elif row_index == 1:
            label_text = f"{0:>{y_label_width}}"
        else:
            label_text = " " * y_label_width
        gutter = _paint(f"  {label_text} ▕", fg=TOKYO_NIGHT["comment"], color=color)
        rows.append(f"{gutter}{bar_segment}")

    axis_prefix = "  " + " " * y_label_width + " ╰"
    axis_line = _paint(axis_prefix + "─" * bar_count + "╴", fg=TOKYO_NIGHT["comment"], color=color)
    rows.append(axis_line)

    label_offset = len(axis_prefix)
    label_chars = [" "] * (label_offset + bar_count + 1)
    left, mid, right = x_axis_labels
    _place_label(label_chars, label_offset, left)
    mid_position = label_offset + max(0, (bar_count - len(mid)) // 2)
    _place_label(label_chars, mid_position, mid)
    right_position = label_offset + max(0, bar_count - len(right))
    _place_label(label_chars, right_position, right)
    rows.append(_paint("".join(label_chars).rstrip(), fg=TOKYO_NIGHT["comment"], color=color))

    return "\n".join(rows)


def _quality_score_bin_counts(scores: Sequence[float], *, bins: int) -> list[int]:
    bin_counts = [0] * bins
    for score in scores:
        clamped = min(max(score, 0.0), 1.0)
        index = min(int(clamped * bins), bins - 1)
        bin_counts[index] += 1
    return bin_counts


def _log_count_bin_counts(counts: Sequence[int], *, bins: int) -> list[int]:
    bin_counts = [0] * bins
    if not counts:
        return bin_counts
    positive = [value for value in counts if value > 0]
    if not positive:
        bin_counts[0] = len(counts)
        return bin_counts
    max_count = max(positive)
    log_max = math.log(max_count) if max_count > 1 else 0.0
    for value in counts:
        if value <= 0 or log_max <= 0:
            bin_counts[0] += 1
            continue
        ratio = math.log(value) / log_max
        index = max(0, min(bins - 1, int(ratio * (bins - 1))))
        bin_counts[index] += 1
    return bin_counts


def _quality_subtitle(scores: Sequence[float], *, bins: int) -> str:
    if not scores:
        return f"0 relics // bins={bins} // span 0.000 → 1.000"
    return (
        f"{len(scores):,} relics // bins={bins} // "
        f"min {min(scores):.3f}  median {_median(scores):.3f}  max {max(scores):.3f}"
    )


def _count_subtitle(counts: Sequence[int], *, bins: int) -> str:
    if not counts:
        return f"0 relics // bins={bins} // log-spaced span 1 → 1"
    positive = [value for value in counts if value > 0]
    if not positive:
        return f"{len(counts):,} relics // bins={bins} // all-zero counts"
    return (
        f"{len(counts):,} relics // bins={bins} // log-spaced "
        f"min {min(positive):,}  median {int(_median(positive)):,}  max {max(positive):,}"
    )


def _log_count_axis_labels(counts: Sequence[int]) -> tuple[str, str, str]:
    positive = [value for value in counts if value > 0]
    if not positive:
        return ("0", "·", "0")
    max_count = max(positive)
    if max_count <= 1:
        return ("1", "1", "1")
    midpoint = int(round(math.exp(math.log(max_count) / 2)))
    return ("1", f"{midpoint:,}", f"{max_count:,}")


def _place_label(buffer: list[str], position: int, text: str) -> None:
    for offset, character in enumerate(text):
        index = position + offset
        if 0 <= index < len(buffer):
            buffer[index] = character


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[midpoint])
    return (float(ordered[midpoint - 1]) + float(ordered[midpoint])) / 2.0


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _stage_default_label(stage: str) -> str:
    return stage.replace("_", " ").upper()


def _compose_columns(left: list[str], right: list[str], *, width: int) -> str:
    left_width = max(_visible_len(line) for line in left)
    min_right_width = 32
    gap = 4
    if width < left_width + gap + min_right_width:
        return "\n".join([*left, "", *right])

    lines: list[str] = []
    total_rows = max(len(left), len(right))
    for index in range(total_rows):
        left_line = left[index] if index < len(left) else ""
        right_line = right[index] if index < len(right) else ""
        pad = " " * max(0, left_width - _visible_len(left_line))
        lines.append(f"{left_line}{pad}{' ' * gap}{right_line}".rstrip())
    return "\n".join(lines)


def _info_line(key: str, value: str, *, color: bool, emphasis: bool = False) -> str:
    key_color = TOKYO_NIGHT["pink"] if emphasis else TOKYO_NIGHT["cyan"]
    value_color = TOKYO_NIGHT["yellow"] if emphasis else TOKYO_NIGHT["fg"]
    key_text = _paint(f"{key:<10}", fg=key_color, bold=True, color=color)
    divider = _paint("::", fg=TOKYO_NIGHT["comment"], color=color)
    value_text = _paint(value, fg=value_color, color=color)
    return f"{key_text} {divider} {value_text}"

def _paint(
    text: str,
    *,
    fg: str | None = None,
    bg: str | None = None,
    bold: bool = False,
    color: bool,
) -> str:
    if not color:
        return text

    codes: list[str] = []
    if bold:
        codes.append("1")
    if fg is not None:
        red, green, blue = _hex_to_rgb(fg)
        codes.append(f"38;2;{red};{green};{blue}")
    if bg is not None:
        red, green, blue = _hex_to_rgb(bg)
        codes.append(f"48;2;{red};{green};{blue}")
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def _format_number(value: int) -> str:
    return f"{value:,}"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def _is_interactive(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty() and os.environ.get("TERM") != "dumb")


def _supports_color(stream: TextIO) -> bool:
    return _is_interactive(stream) and "NO_COLOR" not in os.environ


def _terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(100, 24)).columns
