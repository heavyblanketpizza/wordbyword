from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from wordbyword.example_cleanup import CleanupBatchProgress
    from wordbyword.pipeline import PipelineResult

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
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    color = _supports_color(stream)
    width = _terminal_width()
    line = render_cleanup_progress(progress, model=model, color=color, width=width)
    stream.write("\033[2K\r")
    stream.write(line)
    if progress.phase == "complete" and progress.completed_batches >= progress.total_batches:
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
    color: bool,
    width: int = 100,
) -> str:
    phase_color = PHASE_COLORS["cleanup"]
    tag = _paint(
        " CLEANUP  ",
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
        f"rows {progress.completed_rows}/{progress.total_rows}  "
    )
    tail_plain = f"  {ratio * 100:5.1f}%{suffix_plain}"
    overhead = len(" CLEANUP  ") + 1 + 1 + 1 + len(head_plain) + len(tail_plain) + 2
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
