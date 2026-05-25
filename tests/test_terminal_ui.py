from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from wordbyword.after_class_reports import AfterClassReportProgress
from wordbyword.dictionary_build.example_cleanup import CleanupBatchProgress
from wordbyword.dictionary_build.pipeline import PipelineResult
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import SourceRewriteProgress
from wordbyword.classroom_printout_studio.class_printing.printable import PrintableProgress
from wordbyword.classroom_printout_studio.source_parsing.materials import SourceMaterialProgress
from wordbyword.cli.terminal_ui import (
    render_artifact_drop,
    render_after_class_report_progress,
    render_after_class_report_summary,
    render_boot_screen,
    render_cleanup_progress,
    render_completion_screen,
    render_dictionary_distribution,
    render_live_lexicon_progress,
    render_live_lexicon_summary,
    render_printable_progress,
    render_printable_summary,
    render_source_material_progress,
    render_source_rewrite_progress,
    render_source_rewrite_summary,
    render_stage_update,
    render_usage_signal_summary,
)


class TerminalUiTest(unittest.TestCase):
    def assert_no_old_terminal_ornaments(self, rendered: str) -> None:
        for token in ("▟▙", "cassette", "RELIC", "relic", "░▒▓", "◉", "night-city", "NIGHT CITY"):
            self.assertNotIn(token, rendered)

    def test_render_boot_screen_keeps_mode_without_old_profile_copy(self) -> None:
        rendered = render_boot_screen(
            input_dir=Path("build-dictionary/original"),
            output_dir=Path("build-dictionary"),
            clean_example_sentences=True,
            sentence_cleanup_model="gemma4:31b",
            color=False,
            width=80,
        )

        self.assertIn("WORD BY WORD // READY", rendered)
        self.assertIn("gemma4:31b", rendered)
        self.assertIn("llm cleanup online // gemma4:31b", rendered)
        self.assertIn("scan -> extract -> analyze -> write -> complete", rendered)
        self.assertNotIn("profile ::", rendered)
        self.assertNotIn("style   ::", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_stage_update_plain_mode_is_scan_friendly(self) -> None:
        rendered = render_stage_update("analyze", "tokenizing 422 page/text unit(s)", color=False)

        self.assertIn("ANALYZE", rendered)
        self.assertIn("tokenizing 422 page/text unit(s)", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_source_material_progress_plain_includes_page_bar(self) -> None:
        progress = SourceMaterialProgress(
            phase="ocr",
            source_path=Path("sample.pdf"),
            parser="paddleocr-vl",
            detail="recognized page 2/4",
            completed_pages=2,
            total_pages=4,
        )

        rendered = render_source_material_progress(progress, color=False, width=120)

        self.assertIn("PADDLEOCR", rendered)
        self.assertIn("sample.pdf", rendered)
        self.assertIn("pages 2/4", rendered)
        self.assertIn(" 50.0%", rendered)
        self.assertIn("█", rendered)
        self.assertIn("░", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_source_rewrite_progress_plain_is_scan_friendly(self) -> None:
        progress = SourceRewriteProgress(
            phase="rewrite",
            source_id="lesson",
            detail="rewriting whole document with selected vocabulary",
            source_index=2,
            total_sources=3,
            rewrite_mode="vocabulary_guided",
        )

        rendered = render_source_rewrite_progress(progress, color=False, width=120)

        self.assertIn("REWRITE", rendered)
        self.assertIn("2/3 lesson", rendered)
        self.assertIn("rewriting whole document with selected vocabulary", rendered)
        self.assertNotIn("█", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_source_rewrite_progress_color_includes_ansi(self) -> None:
        progress = SourceRewriteProgress(
            phase="select",
            source_id="lesson",
            detail="selecting vocabulary guidance",
        )

        rendered = render_source_rewrite_progress(progress, color=True, width=120)

        self.assertIn("\x1b[", rendered)
        self.assertIn("SELECT", rendered)
        self.assertIn("lesson", rendered)

    def test_render_source_rewrite_summary_includes_vocabulary_guided_counts(self) -> None:
        rendered = render_source_rewrite_summary(
            title="vocabulary guided",
            summary=(
                ("status", "written"),
                ("dictionary", "build-dictionary/15_final_dictionary.jsonl"),
                ("selected", "20 vocabulary"),
                ("rewritten", "2 pages"),
                ("failed", "0 pages"),
                ("content", "rewritten/lesson/content.md"),
            ),
            source_rows=(
                {
                    "source_id": "lesson",
                    "output_dir": "rewritten/lesson",
                    "status": "written",
                    "selected_vocabulary": 20,
                    "rewritten_pages": 2,
                    "failed_pages": 0,
                },
            ),
            color=False,
            width=120,
        )

        self.assertIn("SOURCE REWRITE // VOCABULARY GUIDED", rendered)
        self.assertIn("build-dictionary/15_final_dictionary.jsonl", rendered)
        self.assertIn("20 vocabulary", rendered)
        self.assertIn("rewritten/lesson/content.md", rendered)
        self.assertIn("lesson", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_printable_progress_plain_is_scan_friendly(self) -> None:
        progress = PrintableProgress(
            phase="plan",
            source_id="popular-medicineprize2025",
            detail="planning printable reading material",
            source_index=2,
            total_sources=3,
        )

        rendered = render_printable_progress(progress, color=False, width=120)

        self.assertIn("PLAN", rendered)
        self.assertIn("2/3 popular-medicineprize2025", rendered)
        self.assertIn("planning printable reading material", rendered)
        self.assertNotIn("█", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_printable_progress_color_includes_ansi(self) -> None:
        progress = PrintableProgress(
            phase="write",
            source_id="lesson",
            detail="writing printable DOCX bundle",
        )

        rendered = render_printable_progress(progress, color=True, width=120)

        self.assertIn("\x1b[", rendered)
        self.assertIn("WRITE", rendered)
        self.assertIn("lesson", rendered)

    def test_render_printable_summary_includes_docx_counts_and_font(self) -> None:
        rendered = render_printable_summary(
            title="DOCX materials",
            summary=(
                ("status", "written"),
                ("output", "classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama"),
                ("written", "3"),
                ("failed", "0"),
            ),
            source_rows=(
                {
                    "source_id": "popular-medicineprize2025",
                    "output_dir": "printable/popular-medicineprize2025",
                    "status": "written",
                    "docx": "printable/popular-medicineprize2025/material.docx",
                    "selected_image_count": 1,
                    "body_font_size_pt": 13,
                },
            ),
            color=False,
            width=120,
        )

        self.assertIn("PRINTABLE CLASS // DOCX MATERIALS", rendered)
        self.assertIn("material.docx", rendered)
        self.assertIn("written", rendered)
        self.assertIn("images 1", rendered)
        self.assertIn("font 13pt", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_after_class_report_progress_plain_is_scan_friendly(self) -> None:
        progress = AfterClassReportProgress(
            phase="analyze",
            source_id="2026-05-22_grade6-quantum-physics-macroscopic-quantum-tunneling",
            detail="building isolated 00-09 heuristic analysis at reports/lesson/heuristic-analysis",
        )

        rendered = render_after_class_report_progress(progress, color=False, width=120)

        self.assertIn("ANALYZE", rendered)
        self.assertIn("2026-05-22_grade6-quantum", rendered)
        self.assertIn("building isolated 00-09 heuristic analysis", rendered)
        self.assertNotIn("█", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_after_class_report_progress_color_includes_ansi(self) -> None:
        progress = AfterClassReportProgress(
            phase="plan",
            source_id="2026-05-22_grade6-quantum-physics",
            detail="generating adult-facing Korean report plan with configured LLM",
        )

        rendered = render_after_class_report_progress(progress, color=True, width=120)

        self.assertIn("\x1b[", rendered)
        self.assertIn("PLAN", rendered)
        self.assertIn("2026-05-22_grade6-quantum", rendered)

    def test_render_after_class_report_summary_includes_docx_vocabulary_and_status(self) -> None:
        rendered = render_after_class_report_summary(
            title="reading report",
            summary=(
                ("status", "written"),
                ("source_id", "2026-05-22_grade6-quantum-physics"),
                ("output", "after-class-reports/2026-05-22_grade6-quantum-physics"),
                (
                    "heuristic analysis",
                    "after-class-reports/2026-05-22_grade6-quantum-physics/heuristic-analysis",
                ),
                ("selected vocabulary", "15"),
                ("docx", "after-class-reports/2026-05-22_grade6-quantum-physics/after_class_report.docx"),
            ),
            source_rows=(
                {
                    "source_id": "2026-05-22_grade6-quantum-physics",
                    "output_dir": "after-class-reports/2026-05-22_grade6-quantum-physics",
                    "status": "written",
                    "selected_vocabulary_count": 15,
                    "docx": "after-class-reports/2026-05-22_grade6-quantum-physics/after_class_report.docx",
                },
            ),
            color=False,
            width=120,
        )

        self.assertIn("AFTER-CLASS REPORT // READING REPORT", rendered)
        self.assertIn("after_class_report.docx", rendered)
        self.assertIn("heuristic-analysis", rendered)
        self.assertIn("vocab 15", rendered)
        self.assertIn("written", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_usage_signal_summary_plain_is_scan_friendly(self) -> None:
        result = SimpleNamespace(
            archive_root=Path("wbw-vocabulary-intelligence/class-session-archive"),
            output_root=Path("wbw-vocabulary-intelligence/usage-signal-build"),
            documents=3,
            extracted_pages=3,
            sentences=337,
            token_occurrences=7809,
            vocabulary_terms=947,
            surface_words=1880,
        )

        rendered = render_usage_signal_summary(result, color=False, width=100)

        self.assertIn("USAGE SIGNALS // WRITTEN", rendered)
        self.assertIn("status     :: written", rendered)
        self.assertIn("documents  :: 3", rendered)
        self.assertIn("tokens     :: 7,809", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_live_lexicon_summary_plain_is_scan_friendly(self) -> None:
        result = SimpleNamespace(
            canonical_source="existing_live_lexicon",
            live_lexicon_path=Path("wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl"),
            supplemental_lexicon_path=Path(
                "wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-supplemental.jsonl"
            ),
            unmatched_usage_output_path=Path(
                "wbw-vocabulary-intelligence/usage-signal-build/10_unmatched_usage_candidates.jsonl"
            ),
            live_lexicon_rows=2160,
            total_class_printouts=3,
            matched_count=470,
            unmatched_count=584,
            supplemental_active_rows=584,
            supplemental_added_count=11,
            supplemental_updated_count=570,
            supplemental_inactive_count=2,
            supplemental_promoted_count=1,
        )

        rendered = render_live_lexicon_summary(result, color=False, width=100)

        self.assertIn("LIVE LEXICON // UPDATED", rendered)
        self.assertIn("source     :: existing live lexicon", rendered)
        self.assertIn("canonical  :: 2,160", rendered)
        self.assertIn("promoted   :: 1", rendered)
        self.assertIn("live files", rendered)
        self.assertNotIn("unmatched  .../10_unmatched_usage_candidates.jsonl", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_live_lexicon_summary_shortens_long_paths(self) -> None:
        result = SimpleNamespace(
            canonical_source="seeded_from_dictionary",
            live_lexicon_path=Path(
                "wbw-vocabulary-intelligence/live-grade-lexicon/very/deep/path/grade1-live-lexicon-canonical.jsonl"
            ),
            supplemental_lexicon_path=Path(
                "wbw-vocabulary-intelligence/live-grade-lexicon/very/deep/path/grade1-live-lexicon-supplemental.jsonl"
            ),
            unmatched_usage_output_path=Path(
                "wbw-vocabulary-intelligence/usage-signal-build/very/deep/path/10_unmatched_usage_candidates.jsonl"
            ),
            live_lexicon_rows=2160,
            total_class_printouts=3,
            matched_count=470,
            unmatched_count=584,
            supplemental_active_rows=584,
            supplemental_added_count=11,
            supplemental_updated_count=570,
            supplemental_inactive_count=2,
            supplemental_promoted_count=1,
        )

        rendered = render_live_lexicon_summary(result, color=False, width=72)

        self.assertIn("...", rendered)
        self.assertTrue(all(len(line) <= 72 for line in rendered.splitlines()))

    def test_render_live_lexicon_summary_color_includes_ansi(self) -> None:
        result = SimpleNamespace(
            canonical_source="existing_live_lexicon",
            live_lexicon_path=Path("live/canonical.jsonl"),
            supplemental_lexicon_path=Path("live/supplemental.jsonl"),
            unmatched_usage_output_path=Path("usage/unmatched.jsonl"),
            live_lexicon_rows=2,
            total_class_printouts=1,
            matched_count=1,
            unmatched_count=1,
            supplemental_active_rows=1,
            supplemental_added_count=1,
            supplemental_updated_count=0,
            supplemental_inactive_count=0,
            supplemental_promoted_count=0,
        )

        rendered = render_live_lexicon_summary(result, color=True, width=100)

        self.assertIn("\x1b[", rendered)
        self.assertIn("LIVE LEXICON // UPDATED", rendered)

    def test_render_live_lexicon_progress_includes_phase_label(self) -> None:
        rendered = render_live_lexicon_progress(
            "maintain",
            "maintaining supplemental rows from 584 unmatched usage candidate(s)",
            color=False,
        )

        self.assertIn("MAINTAIN", rendered)
        self.assertIn("live lexicon :: maintaining supplemental rows", rendered)

    def test_render_completion_screen_plain_mode_keeps_run_stats(self) -> None:
        result = PipelineResult(
            documents=3,
            extracted_pages=422,
            sentences=3421,
            token_occurrences=45155,
            vocabulary_terms=2314,
            surface_words=5727,
            output_dir=Path("build-dictionary"),
        )

        rendered = render_completion_screen(
            result,
            input_dir=Path("build-dictionary/original"),
            include_punctuation=False,
            include_numbers=False,
            clean_example_sentences=False,
            sentence_cleanup_model="gemma4:31b",
            color=False,
            width=120,
        )

        self.assertIn("PIPELINE // COMPLETE", rendered)
        self.assertIn("wordbyword", rendered)
        self.assertIn("documents", rendered)
        self.assertIn("45,155", rendered)
        self.assertIn("-punctuation -numbers", rendered)
        self.assertNotIn("operator", rendered)
        self.assertNotIn("profile", rendered)
        self.assertNotIn("palette", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_completion_screen_color_mode_includes_ansi(self) -> None:
        result = PipelineResult(
            documents=1,
            extracted_pages=2,
            sentences=3,
            token_occurrences=4,
            vocabulary_terms=5,
            surface_words=6,
            output_dir=Path("build-dictionary"),
        )

        rendered = render_completion_screen(
            result,
            input_dir=Path("build-dictionary/original"),
            include_punctuation=True,
            include_numbers=True,
            clean_example_sentences=True,
            sentence_cleanup_model="gemma4:31b",
            color=True,
            width=120,
        )

        self.assertIn("\x1b[", rendered)
        self.assertIn("PIPELINE // COMPLETE", rendered)
        self.assertIn("gemma4:31b", rendered)


    def test_render_cleanup_progress_plain_includes_bar_and_counts(self) -> None:
        progress = CleanupBatchProgress(
            phase="complete",
            batch_index=3,
            total_batches=8,
            completed_batches=3,
            completed_rows=150,
            total_rows=400,
            current_batch_size=50,
        )

        rendered = render_cleanup_progress(progress, model="gemma4:31b", color=False, width=120)

        self.assertIn("CLEANUP", rendered)
        self.assertIn("batch 03/08", rendered)
        self.assertIn("rows 150/400", rendered)
        self.assertIn(" 37.5%", rendered)
        self.assertIn("gemma4:31b", rendered)
        self.assertIn("█", rendered)
        self.assertIn("░", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_cleanup_progress_color_includes_ansi(self) -> None:
        progress = CleanupBatchProgress(
            phase="start",
            batch_index=1,
            total_batches=4,
            completed_batches=0,
            completed_rows=0,
            total_rows=200,
            current_batch_size=50,
        )

        rendered = render_cleanup_progress(progress, model="gemma4:31b", color=True, width=120)

        self.assertIn("\x1b[", rendered)
        self.assertIn("CLEANUP", rendered)
        self.assertIn("batch 01/04", rendered)

    def test_render_cleanup_progress_supports_dictionary_label_and_term_counts(self) -> None:
        progress = CleanupBatchProgress(
            phase="complete",
            batch_index=2,
            total_batches=5,
            completed_batches=2,
            completed_rows=50,
            total_rows=120,
            current_batch_size=25,
        )

        rendered = render_cleanup_progress(
            progress,
            model="gemma4:31b",
            label="dictionary",
            item_label="terms",
            color=False,
            width=120,
        )

        self.assertIn("DICTIONARY", rendered)
        self.assertIn("batch 02/05", rendered)
        self.assertIn("terms 50/120", rendered)
        self.assertIn("gemma4:31b", rendered)


    def test_render_dictionary_distribution_includes_learning_signal_and_count_histograms(self) -> None:
        entries = [
            {"learning_signal_score": -0.45, "count": 1},
            {"learning_signal_score": -0.32, "count": 4},
            {"learning_signal_score": -0.26, "count": 12},
            {"learning_signal_score": -0.18, "count": 22},
            {"learning_signal_score": -0.09, "count": 51},
            {"learning_signal_score": 0.05, "count": 88},
            {"learning_signal_score": 0.12, "count": 144},
            {"learning_signal_score": 0.28, "count": 220},
            {"learning_signal_score": 0.41, "count": 412},
            {"learning_signal_score": 0.47, "count": 980},
        ]

        rendered = render_dictionary_distribution(entries, color=False, width=100, bins=20, height=8)

        self.assertIn("DISTRIBUTION // DICTIONARY", rendered)
        self.assertIn("learning_signal_score distribution", rendered)
        self.assertIn("occurrence count distribution", rendered)
        self.assertIn("-0.50", rendered)
        self.assertIn("0.50", rendered)
        self.assertIn("█", rendered)
        self.assertIn("10 entries", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_dictionary_distribution_handles_empty_entries(self) -> None:
        rendered = render_dictionary_distribution([], color=False, width=80, bins=12, height=6)

        self.assertIn("DISTRIBUTION // DICTIONARY", rendered)
        self.assertIn("0 entries", rendered)
        self.assertIn("no entries", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_dictionary_distribution_reads_legacy_v2_fields(self) -> None:
        entries = [
            {"quality_score_v2": -0.4, "count": 2},
            {"quality_score_v2": 0.0, "count": 20},
            {"quality_score_v2": 0.45, "count": 200},
        ]

        rendered = render_dictionary_distribution(entries, color=False, width=80, bins=12, height=6)

        self.assertIn("learning_signal_score distribution", rendered)
        self.assertNotIn("quality_score_v2 distribution", rendered)
        self.assertIn("centered", rendered)
        self.assertIn("-0.50", rendered)
        self.assertIn("0.50", rendered)
        self.assertIn("occurrence count distribution", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_dictionary_distribution_color_mode_emits_ansi(self) -> None:
        entries = [{"learning_signal_score": 0.0, "count": 10}]

        rendered = render_dictionary_distribution(entries, color=True, width=80, bins=12, height=6)

        self.assertIn("\x1b[", rendered)
        self.assertIn("DISTRIBUTION // DICTIONARY", rendered)

    def test_render_artifact_drop_for_dictionary_includes_distribution(self) -> None:
        entries = [
            {"learning_signal_score": -0.29, "count": 7},
            {"learning_signal_score": 0.12, "count": 88},
            {"learning_signal_score": 0.44, "count": 612},
        ]

        rendered = render_artifact_drop(
            "11_cleaned_dictionary_candidates",
            entries=entries,
            file_label="11_cleaned_dictionary_candidates.jsonl",
            summary=(("relics", "3 headwords"), ("status", "cache locked")),
            color=False,
            width=100,
        )

        self.assertIn("ARTIFACT // CLEANED DICTIONARY", rendered)
        self.assertIn("file       :: 11_cleaned_dictionary_candidates.jsonl", rendered)
        self.assertIn("entries    :: 3 headwords", rendered)
        self.assertIn("status     :: written", rendered)
        self.assertIn("learning_signal_score distribution", rendered)
        self.assertIn("occurrence count distribution", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_artifact_drop_for_raw_dictionary_candidates_includes_distribution(self) -> None:
        entries = [
            {"learning_signal_score": -0.29, "count": 7},
            {"learning_signal_score": 0.12, "count": 88},
            {"learning_signal_score": 0.44, "count": 612},
        ]

        rendered = render_artifact_drop(
            "08_dictionary_candidates",
            entries=entries,
            file_label="08_dictionary_candidates.jsonl",
            summary=(("relics", "3 headwords"), ("status", "replay // no llm calls")),
            color=False,
            width=100,
        )

        self.assertIn("ARTIFACT // DICTIONARY CANDIDATES", rendered)
        self.assertIn("file       :: 08_dictionary_candidates.jsonl", rendered)
        self.assertIn("learning_signal_score distribution", rendered)
        self.assertIn("occurrence count distribution", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_artifact_drop_for_final_dictionary_includes_distribution(self) -> None:
        entries = [
            {"learning_signal_score": -0.29, "count": 7},
            {"learning_signal_score": 0.12, "count": 88},
            {"learning_signal_score": 0.44, "count": 612},
        ]

        rendered = render_artifact_drop(
            "15_final_dictionary",
            entries=entries,
            file_label="15_final_dictionary.jsonl",
            summary=(("relics", "3 headwords"), ("status", "replay // no llm calls")),
            color=False,
            width=100,
        )

        self.assertIn("ARTIFACT // FINAL DICTIONARY", rendered)
        self.assertIn("file       :: 15_final_dictionary.jsonl", rendered)
        self.assertIn("entries    :: 3 headwords", rendered)
        self.assertIn("learning_signal_score distribution", rendered)
        self.assertIn("occurrence count distribution", rendered)
        self.assert_no_old_terminal_ornaments(rendered)

    def test_render_artifact_drop_for_example_sentences_skips_distribution(self) -> None:
        rendered = render_artifact_drop(
            "09_example_sentences",
            file_label="09_example_sentences.csv",
            summary=(("relics", "12 sentences"), ("status", "signal locked")),
            color=False,
            width=100,
        )

        self.assertIn("ARTIFACT // EXAMPLE SENTENCES", rendered)
        self.assertIn("file       :: 09_example_sentences.csv", rendered)
        self.assertIn("entries    :: 12 sentences", rendered)
        self.assertIn("status     :: written", rendered)
        self.assertNotIn("signal locked", rendered)
        self.assertNotIn("learning_signal_score distribution", rendered)
        self.assertNotIn("occurrence count distribution", rendered)
        self.assert_no_old_terminal_ornaments(rendered)


if __name__ == "__main__":
    unittest.main()
