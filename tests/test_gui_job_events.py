from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from wordbyword.gui.job_events import (
    after_class_report_event,
    cleanup_event,
    result_event,
    source_material_event,
)


class GuiJobEventTests(unittest.TestCase):
    def test_cleanup_progress_maps_to_rows(self) -> None:
        progress = SimpleNamespace(
            phase="review",
            completed_batches=2,
            total_batches=4,
            completed_rows=50,
            total_rows=100,
        )

        event = cleanup_event("dictionary", progress, label="examples").to_dict()

        self.assertEqual(event["workflow"], "dictionary")
        self.assertEqual(event["phase"], "review")
        self.assertEqual(event["current"], 50)
        self.assertEqual(event["total"], 100)
        self.assertIn("examples", str(event["detail"]))

    def test_source_material_progress_prefers_page_progress(self) -> None:
        progress = SimpleNamespace(
            phase="ocr",
            source_path=Path("/tmp/source.pdf"),
            detail="page 2",
            source_index=1,
            total_sources=3,
            completed_pages=2,
            total_pages=10,
        )

        event = source_material_event("class_printouts", progress).to_dict()

        self.assertEqual(event["current"], 2)
        self.assertEqual(event["total"], 10)
        self.assertIn("source.pdf", str(event["detail"]))

    def test_after_class_report_progress_includes_artifacts(self) -> None:
        progress = SimpleNamespace(
            phase="write",
            source_id="lesson",
            detail="writing report",
            output_dir=Path("/tmp/report"),
            heuristic_analysis_dir=Path("/tmp/report/heuristic-analysis"),
            docx_path=Path("/tmp/report/after_class_report.docx"),
        )

        event = after_class_report_event("reports", progress).to_dict()

        self.assertEqual(event["artifacts"]["output_dir"], "/tmp/report")
        self.assertEqual(event["artifacts"]["docx_path"], "/tmp/report/after_class_report.docx")

    def test_result_event_is_complete(self) -> None:
        event = result_event("archive", "done", {"output": "/tmp/out"}).to_dict()

        self.assertEqual(event["status"], "complete")
        self.assertEqual(event["artifacts"], {"output": "/tmp/out"})
