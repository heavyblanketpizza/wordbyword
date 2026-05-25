from __future__ import annotations

import math
import os
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TextIO, cast

if TYPE_CHECKING:
    from wordbyword.after_class_reports.report import AfterClassReportProgress
    from wordbyword.class_archive.live_lexicon import LiveLexiconUpdateResult
    from wordbyword.class_archive.usage_signals import UsageSignalBuildResult
    from wordbyword.dictionary_build.example_cleanup import CleanupBatchProgress
    from wordbyword.dictionary_build.pipeline import PipelineResult
    from wordbyword.classroom_printout_studio.class_rewriting.rewrite import SourceRewriteProgress
    from wordbyword.classroom_printout_studio.class_printing.printable import PrintableProgress
    from wordbyword.classroom_printout_studio.source_parsing.materials import SourceMaterialProgress

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
    "start": TOKYO_NIGHT["blue"],
    "render": TOKYO_NIGHT["cyan"],
    "load": TOKYO_NIGHT["purple"],
    "ocr": TOKYO_NIGHT["green"],
    "plan": TOKYO_NIGHT["purple"],
    "select": TOKYO_NIGHT["green"],
    "rewrite": TOKYO_NIGHT["cyan"],
    "maintain": TOKYO_NIGHT["green"],
    "write": TOKYO_NIGHT["orange"],
    "complete": TOKYO_NIGHT["pink"],
    "skip": TOKYO_NIGHT["yellow"],
}

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


def emit_usage_signal_progress(phase: str, detail: str, stream: TextIO | None = None) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(render_usage_signal_progress(phase, detail, color=_supports_color(stream)))
    stream.write("\n")
    stream.flush()
    return True


def render_usage_signal_progress(phase: str, detail: str, *, color: bool) -> str:
    return render_stage_update(phase, f"usage signals :: {detail}", color=color)


def emit_live_lexicon_progress(phase: str, detail: str, stream: TextIO | None = None) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(render_live_lexicon_progress(phase, detail, color=_supports_color(stream)))
    stream.write("\n")
    stream.flush()
    return True


def render_live_lexicon_progress(phase: str, detail: str, *, color: bool) -> str:
    return render_stage_update(phase, f"live lexicon :: {detail}", color=color)


def emit_source_material_progress(
    progress: "SourceMaterialProgress",
    *,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    line = render_source_material_progress(
        progress,
        color=_supports_color(stream),
        width=_terminal_width(),
    )
    if progress.phase in {"render", "ocr"} and progress.total_pages > 0:
        stream.write("\033[2K\r")
        stream.write(line)
        if progress.completed_pages >= progress.total_pages:
            stream.write("\n")
    else:
        stream.write(line)
        stream.write("\n")
    stream.flush()


def emit_source_rewrite_progress(
    progress: "SourceRewriteProgress",
    *,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_source_rewrite_progress(
            progress,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()


def emit_printable_progress(
    progress: "PrintableProgress",
    *,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_printable_progress(
            progress,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()


def emit_after_class_report_progress(
    progress: "AfterClassReportProgress",
    *,
    stream: TextIO | None = None,
) -> None:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return
    stream.write(
        render_after_class_report_progress(
            progress,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
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
    if not interactive:
        stream.write(render_completion_summary_line(result))
        stream.write("\n")
        stream.flush()
        return

    color = _supports_color(stream)
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


def render_completion_summary_line(result: "PipelineResult") -> str:
    return (
        "wordbyword: pipeline complete; "
        f"documents={result.documents:,}; "
        f"sentences={result.sentences:,}; "
        f"vocabulary={result.vocabulary_terms:,}; "
        f"output={result.output_dir}"
    )


def render_boot_screen(
    *,
    input_dir: Path,
    output_dir: Path,
    clean_example_sentences: bool,
    sentence_cleanup_model: str,
    color: bool,
    width: int = 100,
) -> str:
    mode = "llm cleanup online" if clean_example_sentences else "deterministic pipeline only"
    if clean_example_sentences:
        mode = f"{mode} // {sentence_cleanup_model}"

    banner = _banner("WORD BY WORD // READY", accent=TOKYO_NIGHT["cyan"], color=color)
    underline = _summary_rule(color=color, width=width)
    steps = _paint(
        "flow      :: scan -> extract -> analyze -> write -> complete",
        fg=TOKYO_NIGHT["purple"],
        bold=True,
        color=color,
    )
    details = [
        _info_line("input", str(input_dir), color=color),
        _info_line("output", str(output_dir), color=color),
        _info_line("mode", mode, color=color),
    ]
    lines = [banner, underline, steps, *details]
    return "\n".join(lines)


def render_stage_update(phase: str, detail: str, *, color: bool) -> str:
    phase_color = PHASE_COLORS.get(phase, TOKYO_NIGHT["blue"])
    tag = _paint(f" {phase.upper():<8} ", fg=TOKYO_NIGHT["bg"], bg=phase_color, bold=True, color=color)
    arrow = _paint(">", fg=phase_color, bold=True, color=color)
    message = _paint(detail, fg=TOKYO_NIGHT["fg"], color=color)
    return f"{tag} {arrow} {message}"


def render_source_material_progress(
    progress: "SourceMaterialProgress",
    *,
    color: bool,
    width: int = 100,
) -> str:
    phase_color = PHASE_COLORS.get(progress.phase, TOKYO_NIGHT["blue"])
    label = "PADDLEOCR" if progress.phase == "ocr" else progress.phase.upper()
    tag_text = f" {label[:10]:<10} "
    tag = _paint(
        tag_text,
        fg=TOKYO_NIGHT["bg"],
        bg=phase_color,
        bold=True,
        color=color,
    )
    arrow_glyph = ">"
    arrow = _paint(arrow_glyph, fg=phase_color, bold=True, color=color)

    source_prefix = (
        f"{progress.source_index}/{progress.total_sources} "
        if progress.total_sources > 1
        else ""
    )
    source_label = _shorten_text(
        f"{source_prefix}{progress.source_path.name}",
        max_width=max(16, min(36, width // 3)),
    )
    if progress.phase in {"render", "ocr"} and progress.total_pages > 0:
        total_pages = max(progress.total_pages, 1)
        completed_pages = max(0, min(progress.completed_pages, total_pages))
        ratio = completed_pages / total_pages
        head_plain = f"{source_label}  pages {completed_pages}/{progress.total_pages}  "
        tail_plain = f"  {ratio * 100:5.1f}%"
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
        return (
            f"{tag} {arrow} {head_text}{bracket_open}{bar_filled}{bar_empty}"
            f"{bracket_close}  {pct_text}"
        )

    detail = progress.detail or progress.parser
    message = f"{source_label} :: {detail}"
    return f"{tag} {arrow} {_paint(message, fg=TOKYO_NIGHT['fg'], color=color)}"


def render_after_class_report_progress(
    progress: "AfterClassReportProgress",
    *,
    color: bool,
    width: int = 100,
) -> str:
    phase_color = PHASE_COLORS.get(progress.phase, TOKYO_NIGHT["blue"])
    tag_text = f" {progress.phase.upper()[:10]:<10} "
    tag = _paint(
        tag_text,
        fg=TOKYO_NIGHT["bg"],
        bg=phase_color,
        bold=True,
        color=color,
    )
    arrow_glyph = ">"
    arrow = _paint(arrow_glyph, fg=phase_color, bold=True, color=color)
    source_label = _shorten_text(
        progress.source_id,
        max_width=max(16, min(36, width // 3)),
    )
    message = f"{source_label} :: {progress.detail}"
    return f"{tag} {arrow} {_paint(message, fg=TOKYO_NIGHT['fg'], color=color)}"


def render_printable_progress(
    progress: "PrintableProgress",
    *,
    color: bool,
    width: int = 100,
) -> str:
    phase_color = PHASE_COLORS.get(progress.phase, TOKYO_NIGHT["blue"])
    tag_text = f" {progress.phase.upper()[:10]:<10} "
    tag = _paint(
        tag_text,
        fg=TOKYO_NIGHT["bg"],
        bg=phase_color,
        bold=True,
        color=color,
    )
    arrow_glyph = ">"
    arrow = _paint(arrow_glyph, fg=phase_color, bold=True, color=color)
    source_prefix = (
        f"{progress.source_index}/{progress.total_sources} "
        if progress.total_sources > 1
        else ""
    )
    source_label = _shorten_text(
        f"{source_prefix}{progress.source_id}",
        max_width=max(16, min(36, width // 3)),
    )
    message = f"{source_label} :: {progress.detail}"
    return f"{tag} {arrow} {_paint(message, fg=TOKYO_NIGHT['fg'], color=color)}"


def render_source_rewrite_progress(
    progress: "SourceRewriteProgress",
    *,
    color: bool,
    width: int = 100,
) -> str:
    phase_color = PHASE_COLORS.get(progress.phase, TOKYO_NIGHT["blue"])
    tag_text = f" {progress.phase.upper()[:10]:<10} "
    tag = _paint(
        tag_text,
        fg=TOKYO_NIGHT["bg"],
        bg=phase_color,
        bold=True,
        color=color,
    )
    arrow_glyph = ">"
    arrow = _paint(arrow_glyph, fg=phase_color, bold=True, color=color)
    source_prefix = (
        f"{progress.source_index}/{progress.total_sources} "
        if progress.total_sources > 1
        else ""
    )
    source_label = _shorten_text(
        f"{source_prefix}{progress.source_id}",
        max_width=max(16, min(36, width // 3)),
    )
    message = f"{source_label} :: {progress.detail}"
    return f"{tag} {arrow} {_paint(message, fg=TOKYO_NIGHT['fg'], color=color)}"


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
    arrow_glyph = ">"
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
    header = "wordbyword"
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

    lines = [
        _banner("PIPELINE // COMPLETE", accent=TOKYO_NIGHT["pink"], color=color),
        _summary_rule(color=color, width=width),
        *info_lines,
        _summary_rule(color=color, width=width),
    ]
    return "\n".join(lines)


def emit_printable_summary(
    *,
    title: str,
    summary: Sequence[tuple[str, str]],
    source_rows: Sequence[Mapping[str, object]] = (),
    skipped_rows: Sequence[Mapping[str, object]] = (),
    stream: TextIO | None = None,
) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(
        render_printable_summary(
            title=title,
            summary=summary,
            source_rows=source_rows,
            skipped_rows=skipped_rows,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()
    return True


def emit_after_class_report_summary(
    *,
    title: str,
    summary: Sequence[tuple[str, str]],
    source_rows: Sequence[Mapping[str, object]] = (),
    stream: TextIO | None = None,
) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(
        render_after_class_report_summary(
            title=title,
            summary=summary,
            source_rows=source_rows,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()
    return True


def emit_usage_signal_summary(
    result: "UsageSignalBuildResult",
    *,
    stream: TextIO | None = None,
) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(
        render_usage_signal_summary(
            result,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()
    return True


def render_usage_signal_summary(
    result: "UsageSignalBuildResult",
    *,
    color: bool,
    width: int = 100,
) -> str:
    return _render_lexicon_summary_panel(
        banner_text="USAGE SIGNALS // WRITTEN",
        subtitle="class archive scan complete",
        accent=TOKYO_NIGHT["blue"],
        subtitle_color=TOKYO_NIGHT["cyan"],
        summary=(
            ("status", "written"),
            ("archive", _shorten_path_end(str(result.archive_root), max_width=_summary_value_width(width))),
            ("output", _shorten_path_end(str(result.output_root), max_width=_summary_value_width(width))),
            ("documents", _format_number(result.documents)),
            ("text units", _format_number(result.extracted_pages)),
            ("sentences", _format_number(result.sentences)),
            ("tokens", _format_number(result.token_occurrences)),
            ("vocabulary", _format_number(result.vocabulary_terms)),
            ("surface", _format_number(result.surface_words)),
        ),
        color=color,
        width=width,
    )


def emit_live_lexicon_summary(
    result: "LiveLexiconUpdateResult",
    *,
    stream: TextIO | None = None,
) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(
        render_live_lexicon_summary(
            result,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()
    return True


def render_live_lexicon_summary(
    result: "LiveLexiconUpdateResult",
    *,
    color: bool,
    width: int = 100,
) -> str:
    value_width = _summary_value_width(width)
    rule = _summary_rule(color=color, width=width)
    lines = [
        _render_lexicon_summary_panel(
            banner_text="LIVE LEXICON // UPDATED",
            subtitle="canonical and supplemental rows maintained",
            accent=TOKYO_NIGHT["green"],
            subtitle_color=TOKYO_NIGHT["purple"],
            summary=(
                ("status", "updated"),
                ("source", _live_lexicon_source_label(str(result.canonical_source))),
                ("printouts", _format_number(result.total_class_printouts)),
                ("canonical", _format_number(result.live_lexicon_rows)),
                ("matched", _format_number(result.matched_count)),
                ("unmatched", _format_number(result.unmatched_count)),
                ("supp act", _format_number(result.supplemental_active_rows)),
                ("supp add", _format_number(result.supplemental_added_count)),
                ("supp upd", _format_number(result.supplemental_updated_count)),
                ("supp off", _format_number(result.supplemental_inactive_count)),
                ("promoted", _format_number(result.supplemental_promoted_count)),
            ),
            color=color,
            width=width,
        ),
        _paint("| live files", fg=TOKYO_NIGHT["cyan"], bold=True, color=color),
        _paint(
            f"  - canonical  {_shorten_path_end(str(result.live_lexicon_path), max_width=value_width)}",
            fg=TOKYO_NIGHT["fg"],
            color=color,
        ),
        _paint(
            f"  - supplement {_shorten_path_end(str(result.supplemental_lexicon_path), max_width=value_width)}",
            fg=TOKYO_NIGHT["fg"],
            color=color,
        ),
        rule,
    ]
    return "\n".join(lines)


def render_after_class_report_summary(
    *,
    title: str,
    summary: Sequence[tuple[str, str]],
    source_rows: Sequence[Mapping[str, object]] = (),
    color: bool,
    width: int = 100,
) -> str:
    accent = TOKYO_NIGHT["purple"]
    banner = _banner(f"AFTER-CLASS REPORT // {title.upper()}", accent=accent, color=color)
    rule = _summary_rule(color=color, width=width)
    lines = [
        banner,
        _info_line("note", "adult-facing reading report written", color=color),
        rule,
    ]
    lines.extend(_info_line(key, value, color=color, emphasis=key == "status") for key, value in summary)
    if source_rows:
        lines.append(rule)
        lines.append(_paint("| report bundle", fg=TOKYO_NIGHT["cyan"], bold=True, color=color))
        for row in source_rows:
            lines.append(_after_class_report_summary_row(row, color=color, width=width))
    lines.append(rule)
    return "\n".join(lines)


def _after_class_report_summary_row(row: Mapping[str, object], *, color: bool, width: int) -> str:
    source_id = _shorten_text(str(row.get("source_id", "")), max_width=max(12, min(28, width // 3)))
    output_dir = _shorten_text(str(row.get("output_dir", "")), max_width=max(24, min(48, width // 2)))
    details = []
    selected = row.get("selected_vocabulary_count")
    if selected is not None:
        details.append(f"vocab {selected}")
    docx = str(row.get("docx", "") or "")
    if docx:
        details.append(f"docx {_shorten_path_end(docx, max_width=max(18, min(38, width // 3)))}")
    failure = str(row.get("failure_reason", "") or "")
    if failure:
        details.append(f"failure {_shorten_text(failure, max_width=max(18, min(38, width // 3)))}")
    status = str(row.get("status", "")).strip()
    suffix = " // ".join(part for part in [status, ", ".join(details)] if part)
    suffix_text = f" ({suffix})" if suffix else ""
    return _paint(f"  - {source_id}: {output_dir}{suffix_text}", fg=TOKYO_NIGHT["fg"], color=color)


def render_printable_summary(
    *,
    title: str,
    summary: Sequence[tuple[str, str]],
    source_rows: Sequence[Mapping[str, object]] = (),
    skipped_rows: Sequence[Mapping[str, object]] = (),
    color: bool,
    width: int = 100,
) -> str:
    accent = TOKYO_NIGHT["orange"]
    banner = _banner(f"PRINTABLE CLASS // {title.upper()}", accent=accent, color=color)
    rule = _summary_rule(color=color, width=width)
    lines = [
        banner,
        _info_line("note", "editable DOCX written for class use", color=color),
        rule,
    ]
    lines.extend(_info_line(key, value, color=color, emphasis=key == "status") for key, value in summary)
    if source_rows:
        lines.append(rule)
        lines.append(_paint("| DOCX bundles", fg=TOKYO_NIGHT["cyan"], bold=True, color=color))
        for row in source_rows:
            lines.append(_printable_summary_row(row, color=color, width=width))
    if skipped_rows:
        lines.append(rule)
        lines.append(_paint("| skipped bundles", fg=TOKYO_NIGHT["yellow"], bold=True, color=color))
        for row in skipped_rows:
            source = _shorten_text(str(row.get("source_dir", "")), max_width=max(18, min(42, width // 2)))
            reason = str(row.get("reason", ""))
            lines.append(_paint(f"  - skipped {source}: {reason}", fg=TOKYO_NIGHT["fg"], color=color))
    lines.append(rule)
    return "\n".join(lines)


def _printable_summary_row(row: Mapping[str, object], *, color: bool, width: int) -> str:
    source_id = _shorten_text(str(row.get("source_id", "")), max_width=max(12, min(24, width // 4)))
    output_dir = _shorten_text(str(row.get("output_dir", "")), max_width=max(24, min(48, width // 2)))
    details = []
    selected_images = row.get("selected_image_count")
    if selected_images is not None:
        details.append(f"images {selected_images}")
    body_font_size = row.get("body_font_size_pt")
    if body_font_size is not None:
        details.append(f"font {body_font_size}pt")
    docx = str(row.get("docx", "") or "")
    if docx:
        details.append(f"docx {_shorten_path_end(docx, max_width=max(18, min(38, width // 3)))}")
    failure = str(row.get("failure_reason", "") or "")
    if failure:
        details.append(f"failure {_shorten_text(failure, max_width=max(18, min(38, width // 3)))}")
    status = str(row.get("status", "")).strip()
    suffix = " // ".join(part for part in [status, ", ".join(details)] if part)
    suffix_text = f" ({suffix})" if suffix else ""
    return _paint(f"  - {source_id}: {output_dir}{suffix_text}", fg=TOKYO_NIGHT["fg"], color=color)


def emit_source_rewrite_summary(
    *,
    title: str,
    summary: Sequence[tuple[str, str]],
    source_rows: Sequence[Mapping[str, object]] = (),
    skipped_rows: Sequence[Mapping[str, object]] = (),
    stream: TextIO | None = None,
) -> bool:
    stream = sys.stdout if stream is None else stream
    if not _is_interactive(stream):
        return False
    stream.write(
        render_source_rewrite_summary(
            title=title,
            summary=summary,
            source_rows=source_rows,
            skipped_rows=skipped_rows,
            color=_supports_color(stream),
            width=_terminal_width(),
        )
    )
    stream.write("\n")
    stream.flush()
    return True


def render_source_rewrite_summary(
    *,
    title: str,
    summary: Sequence[tuple[str, str]],
    source_rows: Sequence[Mapping[str, object]] = (),
    skipped_rows: Sequence[Mapping[str, object]] = (),
    color: bool,
    width: int = 100,
) -> str:
    accent = TOKYO_NIGHT["green"]
    banner = _banner(f"SOURCE REWRITE // {title.upper()}", accent=accent, color=color)
    rule = _summary_rule(color=color, width=width)
    lines = [
        banner,
        _info_line("note", "class source rewritten for classroom use", color=color),
        rule,
    ]
    lines.extend(_info_line(key, value, color=color, emphasis=key == "status") for key, value in summary)
    if source_rows:
        lines.append(rule)
        lines.append(_paint("| source bundles", fg=TOKYO_NIGHT["cyan"], bold=True, color=color))
        for row in source_rows:
            lines.append(_source_rewrite_summary_row(row, color=color, width=width))
    if skipped_rows:
        lines.append(rule)
        lines.append(_paint("| skipped bundles", fg=TOKYO_NIGHT["yellow"], bold=True, color=color))
        for row in skipped_rows:
            source = _shorten_text(str(row.get("source_dir", "")), max_width=max(18, min(42, width // 2)))
            reason = str(row.get("reason", ""))
            lines.append(_paint(f"  - skipped {source}: {reason}", fg=TOKYO_NIGHT["fg"], color=color))
    lines.append(rule)
    return "\n".join(lines)


def _source_rewrite_summary_row(row: Mapping[str, object], *, color: bool, width: int) -> str:
    source_id = _shorten_text(str(row.get("source_id", "")), max_width=max(12, min(24, width // 4)))
    output_dir = _shorten_text(str(row.get("output_dir", "")), max_width=max(24, min(48, width // 2)))
    details = []
    selected = row.get("selected_vocabulary")
    if selected is not None:
        details.append(f"selected {selected}")
    rewritten = row.get("rewritten_pages")
    if rewritten is not None:
        details.append(f"rewritten {rewritten}")
    failed = row.get("failed_pages")
    if failed is not None:
        details.append(f"failed {failed}")
    status = str(row.get("status", "")).strip()
    suffix = " // ".join(part for part in [status, ", ".join(details)] if part)
    suffix_text = f" ({suffix})" if suffix else ""
    return _paint(f"  - {source_id}: {output_dir}{suffix_text}", fg=TOKYO_NIGHT["fg"], color=color)


HISTOGRAM_FRACTIONS = " ▁▂▃▄▅▆▇█"

ARTIFACT_LABELS = {
    "08_dictionary_candidates": ("ARTIFACT // DICTIONARY CANDIDATES", TOKYO_NIGHT["pink"]),
    "09_example_sentences": ("ARTIFACT // EXAMPLE SENTENCES", TOKYO_NIGHT["cyan"]),
    "11_cleaned_dictionary_candidates": ("ARTIFACT // CLEANED DICTIONARY", TOKYO_NIGHT["pink"]),
    "12_dictionary_candidates_with_cleaned_examples": ("ARTIFACT // LINKED EXAMPLES", TOKYO_NIGHT["orange"]),
    "13_vocabulary_with_generated_examples": ("ARTIFACT // GENERATED EXAMPLES", TOKYO_NIGHT["green"]),
    "14_vocabulary_with_meanings": ("ARTIFACT // VOCABULARY MEANINGS", TOKYO_NIGHT["purple"]),
    "15_final_dictionary": ("ARTIFACT // FINAL DICTIONARY", TOKYO_NIGHT["yellow"]),
}


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
    artifact_title, accent = ARTIFACT_LABELS.get(stage, ("ARTIFACT // WRITTEN", TOKYO_NIGHT["cyan"]))
    label = file_label or _stage_default_label(stage)
    banner = _banner(artifact_title, accent=accent, color=color)
    rule = _summary_rule(color=color, width=width)

    info_lines: list[str] = []
    info_lines.append(_info_line("file", label, color=color))
    for key, value in summary:
        info_lines.append(_artifact_info_line(key, value, color=color))

    parts: list[str] = [banner, rule]
    if info_lines:
        parts.extend(info_lines)
        parts.append(rule)
    if entries is not None and stage in {
        "08_dictionary_candidates",
        "11_cleaned_dictionary_candidates",
        "12_dictionary_candidates_with_cleaned_examples",
        "13_vocabulary_with_generated_examples",
        "14_vocabulary_with_meanings",
        "15_final_dictionary",
    }:
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
    """Render compact histograms for dictionary candidates.

    First chart: x-axis learning_signal_score (-0.500 .. 0.500), y-axis count of vocab in each bin.
    Final chart: x-axis occurrence count (log-spaced), y-axis count of vocab in each bin.
    """

    safe_bins = max(8, min(bins, max(8, width - 12)))
    safe_height = max(4, height)

    learning_signal_scores = [_row_learning_signal_score(entry) for entry in entries]
    counts = [_safe_int(entry.get("count")) for entry in entries]

    header = _banner("DISTRIBUTION // DICTIONARY", accent=TOKYO_NIGHT["purple"], color=color)
    rule = _summary_rule(color=color, width=width)

    learning_signal_panel = _render_histogram_panel(
        title="learning_signal_score distribution",
        subtitle=_learning_signal_subtitle(learning_signal_scores, bins=safe_bins),
        bin_counts=_learning_signal_bin_counts(learning_signal_scores, bins=safe_bins),
        x_axis_labels=("-0.50", "0.00", "0.50"),
        height=safe_height,
        color=color,
        bar_color=TOKYO_NIGHT["cyan"],
        accent_color=TOKYO_NIGHT["pink"],
    )
    panels = [header, rule, learning_signal_panel]
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
    panels.extend(["", count_panel, rule])

    return "\n".join(panels)


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
    title_line = _paint(f"| {title}", fg=accent_color, bold=True, color=color)
    subtitle_line = _paint(f"|   {subtitle}", fg=TOKYO_NIGHT["comment"], color=color)

    if not bin_counts or all(value == 0 for value in bin_counts):
        empty = _paint("|   no entries", fg=TOKYO_NIGHT["comment"], color=color)
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
        gutter = _paint(f"  {label_text} |", fg=TOKYO_NIGHT["comment"], color=color)
        rows.append(f"{gutter}{bar_segment}")

    axis_prefix = "  " + " " * y_label_width + " +"
    axis_line = _paint(axis_prefix + "-" * bar_count + "-", fg=TOKYO_NIGHT["comment"], color=color)
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


def _learning_signal_bin_counts(scores: Sequence[float], *, bins: int) -> list[int]:
    bin_counts = [0] * bins
    for score in scores:
        clamped = min(max(score, -0.5), 0.5)
        ratio = clamped + 0.5
        index = min(int(ratio * bins), bins - 1)
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


def _learning_signal_subtitle(scores: Sequence[float], *, bins: int) -> str:
    if not scores:
        return f"0 entries // bins={bins} // span -0.500 -> 0.500"
    return (
        f"{len(scores):,} entries // bins={bins} // centered "
        f"min {min(scores):.3f}  median {_median(scores):.3f}  max {max(scores):.3f}"
    )


def _count_subtitle(counts: Sequence[int], *, bins: int) -> str:
    if not counts:
        return f"0 entries // bins={bins} // log-spaced span 1 -> 1"
    positive = [value for value in counts if value > 0]
    if not positive:
        return f"{len(counts):,} entries // bins={bins} // all-zero counts"
    return (
        f"{len(counts):,} entries // bins={bins} // log-spaced "
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
        return float(cast(str | bytes | bytearray | int | float, value))
    except (TypeError, ValueError):
        return 0.0


def _row_learning_signal_score(row: Mapping[str, object]) -> float:
    if "learning_signal_score" in row:
        return _clamped_float(row.get("learning_signal_score"), minimum=-0.5, maximum=0.5)
    if "quality_score_v2" in row:
        return _clamped_float(row.get("quality_score_v2"), minimum=-0.5, maximum=0.5)
    if "quality_score" in row:
        return _clamped_float(row.get("quality_score"), minimum=0.0, maximum=1.0) - 0.5
    return 0.0


def _clamped_float(value: object, *, minimum: float, maximum: float) -> float:
    return min(max(_safe_float(value), minimum), maximum)


def _safe_int(value: object) -> int:
    try:
        return int(cast(str | bytes | bytearray | int | float, value))
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


def _render_lexicon_summary_panel(
    *,
    banner_text: str,
    subtitle: str,
    accent: str,
    subtitle_color: str,
    summary: Sequence[tuple[str, str]],
    color: bool,
    width: int,
) -> str:
    banner = _banner(banner_text, accent=accent, color=color)
    rule = _summary_rule(color=color, width=width)
    lines = [
        banner,
        _paint(f"note      :: {subtitle}", fg=subtitle_color, bold=True, color=color),
        rule,
    ]
    lines.extend(_info_line(key, value, color=color, emphasis=key == "status") for key, value in summary)
    lines.append(rule)
    return "\n".join(lines)


def _banner(text: str, *, accent: str, color: bool) -> str:
    return _paint(
        f" {text} ",
        fg=TOKYO_NIGHT["bg"],
        bg=accent,
        bold=True,
        color=color,
    )


def _summary_rule(*, color: bool, width: int) -> str:
    return _paint("-" * min(width, 72), fg=TOKYO_NIGHT["comment"], color=color)


def _summary_value_width(width: int) -> int:
    return max(20, min(64, width - 22))


def _live_lexicon_source_label(value: str) -> str:
    if value == "seeded_from_dictionary":
        return "seeded from dictionary"
    if value == "existing_live_lexicon":
        return "existing live lexicon"
    return value.replace("_", " ")


def _info_line(key: str, value: str, *, color: bool, emphasis: bool = False) -> str:
    key_color = TOKYO_NIGHT["pink"] if emphasis else TOKYO_NIGHT["cyan"]
    value_color = TOKYO_NIGHT["yellow"] if emphasis else TOKYO_NIGHT["fg"]
    key_text = _paint(f"{key:<10}", fg=key_color, bold=True, color=color)
    divider = _paint("::", fg=TOKYO_NIGHT["comment"], color=color)
    value_text = _paint(value, fg=value_color, color=color)
    return f"{key_text} {divider} {value_text}"


def _artifact_info_line(key: str, value: str, *, color: bool) -> str:
    display_key = "entries" if key == "relics" else key
    display_value = "written" if key == "status" and value in {"cache locked", "signal locked"} else value
    return _info_line(display_key, display_value, color=color, emphasis=display_key == "status")


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


def _shorten_text(text: str, *, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    return f"{text[: max_width - 3]}..."


def _shorten_path_end(text: str, *, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    name = Path(text).name
    if not name or len(name) + 4 > max_width:
        return _shorten_text(text, max_width=max_width)
    return f".../{name}"


def _is_interactive(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty() and os.environ.get("TERM") != "dumb")


def _supports_color(stream: TextIO) -> bool:
    return _is_interactive(stream) and "NO_COLOR" not in os.environ


def _terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(100, 24)).columns
