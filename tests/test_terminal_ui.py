from __future__ import annotations

import unittest
from pathlib import Path

from wordbyword.example_cleanup import CleanupBatchProgress
from wordbyword.pipeline import PipelineResult
from wordbyword.terminal_ui import (
    render_boot_screen,
    render_cleanup_progress,
    render_completion_screen,
    render_stage_update,
)


class TerminalUiTest(unittest.TestCase):
    def test_render_boot_screen_keeps_mode_without_old_profile_copy(self) -> None:
        rendered = render_boot_screen(
            input_dir=Path("original"),
            output_dir=Path("output"),
            clean_example_sentences=True,
            sentence_cleanup_model="gemma4:31b",
            color=False,
            width=80,
        )

        self.assertIn("TOKYO NIGHT RELIC BOOT", rendered)
        self.assertIn("gemma4:31b", rendered)
        self.assertIn("mode    :: ollama cleanup online // gemma4:31b", rendered)
        self.assertNotIn("profile ::", rendered)
        self.assertNotIn("style   ::", rendered)

    def test_render_stage_update_plain_mode_is_scan_friendly(self) -> None:
        rendered = render_stage_update("analyze", "tokenizing 422 page/text unit(s)", color=False)

        self.assertIn("ANALYZE", rendered)
        self.assertIn("tokenizing 422 page/text unit(s)", rendered)
        self.assertNotIn("\x1b[", rendered)

    def test_render_completion_screen_plain_mode_keeps_run_stats(self) -> None:
        result = PipelineResult(
            documents=3,
            extracted_pages=422,
            sentences=3421,
            token_occurrences=45155,
            vocabulary_terms=2314,
            surface_words=5727,
            output_dir=Path("output"),
        )

        rendered = render_completion_screen(
            result,
            input_dir=Path("original"),
            include_punctuation=False,
            include_numbers=False,
            clean_example_sentences=False,
            sentence_cleanup_model="gemma4:31b",
            color=False,
            width=120,
        )

        self.assertIn("RELIC LINK STABLE // OUTPUT STACK WRITTEN", rendered)
        self.assertIn("wordbyword@night-city", rendered)
        self.assertIn("documents", rendered)
        self.assertIn("45,155", rendered)
        self.assertIn("-punctuation -numbers", rendered)
        self.assertIn("NIGHT CITY SIGNAL", rendered)
        self.assertNotIn("operator", rendered)
        self.assertNotIn("profile", rendered)
        self.assertNotIn("palette", rendered)

    def test_render_completion_screen_color_mode_includes_ansi(self) -> None:
        result = PipelineResult(
            documents=1,
            extracted_pages=2,
            sentences=3,
            token_occurrences=4,
            vocabulary_terms=5,
            surface_words=6,
            output_dir=Path("output"),
        )

        rendered = render_completion_screen(
            result,
            input_dir=Path("original"),
            include_punctuation=True,
            include_numbers=True,
            clean_example_sentences=True,
            sentence_cleanup_model="gemma4:31b",
            color=True,
            width=120,
        )

        self.assertIn("\x1b[", rendered)
        self.assertIn("RELIC LINK // ACTIVE", rendered)
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


if __name__ == "__main__":
    unittest.main()
