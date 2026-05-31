from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.gui.review import (
    REVIEW_KIND_AFTER_CLASS_REPORT,
    REVIEW_KIND_CLASS_PRINTOUT,
    REVIEW_KIND_DICTIONARY,
    REVIEW_KIND_LIVE_LEXICON,
    REVIEW_STATUS_FAILED,
    REVIEW_STATUS_MISSING,
    REVIEW_STATUS_NEEDS_REVIEW,
    build_review_snapshot,
)
from wordbyword.gui.settings import default_settings


class GuiReviewSnapshotTests(unittest.TestCase):
    def test_successful_class_printout_requires_review_when_quality_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            printout_dir = root / "printouts" / "lesson-a"
            printout_dir.mkdir(parents=True)
            (printout_dir / "material.docx").write_bytes(b"docx")
            (printout_dir / "quality_report.json").write_text(
                json.dumps(
                    {
                        "source_id": "lesson-a",
                        "status": "written",
                        "requires_human_revision": True,
                        "section_count": 8,
                        "selected_image_count": 2,
                    }
                ),
                encoding="utf-8",
            )
            settings = default_settings()
            settings["print_output_root"] = str(root / "printouts")
            settings["dictionary_output_dir"] = str(root / "dictionary")
            settings["live_lexicon_path"] = str(root / "lexicon" / "canonical.jsonl")

            snapshot = build_review_snapshot(settings)
            item = _first_item(snapshot, REVIEW_KIND_CLASS_PRINTOUT)

        self.assertEqual(item["status"], REVIEW_STATUS_NEEDS_REVIEW)
        self.assertEqual(item["title"], "lesson-a")
        self.assertIn("rebuild", item["available_actions"])
        self.assertEqual(_metric(item, "sections"), 8)
        self.assertEqual(_metric(item, "images"), 2)

    def test_failed_class_printout_when_failures_file_has_rows(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            printout_dir = root / "printouts" / "lesson-b"
            printout_dir.mkdir(parents=True)
            (printout_dir / "material.docx").write_bytes(b"docx")
            (printout_dir / "quality_report.json").write_text(
                json.dumps({"source_id": "lesson-b", "status": "written"}),
                encoding="utf-8",
            )
            (printout_dir / "failures.jsonl").write_text('{"error": "bad"}\n', encoding="utf-8")
            settings = default_settings()
            settings["print_output_root"] = str(root / "printouts")

            snapshot = build_review_snapshot(settings)
            item = _first_item(snapshot, REVIEW_KIND_CLASS_PRINTOUT)

        self.assertEqual(item["status"], REVIEW_STATUS_FAILED)
        self.assertEqual(_metric(item, "failures"), 1)
        self.assertEqual(snapshot["summary"][REVIEW_STATUS_FAILED], 1)

    def test_after_class_report_exposes_selected_vocabulary_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            report_dir = root / "reports" / "lesson-c"
            report_dir.mkdir(parents=True)
            (report_dir / "after_class_report.docx").write_bytes(b"docx")
            (report_dir / "quality_report.json").write_text(
                json.dumps(
                    {
                        "source_id": "lesson-c",
                        "status": "written",
                        "requires_human_revision": True,
                        "selected_vocabulary_count": 15,
                        "deep_teaching_card_count": 5,
                    }
                ),
                encoding="utf-8",
            )
            settings = default_settings()
            settings["after_class_report_output_root"] = str(root / "reports")

            snapshot = build_review_snapshot(settings)
            item = _first_item(snapshot, REVIEW_KIND_AFTER_CLASS_REPORT)

        self.assertEqual(item["status"], REVIEW_STATUS_NEEDS_REVIEW)
        self.assertEqual(snapshot["summary"]["reports"], 1)
        self.assertEqual(_metric(item, "vocabulary"), 15)
        self.assertEqual(_metric(item, "cards"), 5)

    def test_missing_dictionary_and_live_lexicon_become_missing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            settings = default_settings()
            settings["dictionary_output_dir"] = str(root / "dictionary")
            settings["live_lexicon_path"] = str(root / "lexicon" / "canonical.jsonl")

            snapshot = build_review_snapshot(settings)
            dictionary = _first_item(snapshot, REVIEW_KIND_DICTIONARY)
            live_lexicon = _first_item(snapshot, REVIEW_KIND_LIVE_LEXICON)

        self.assertEqual(dictionary["status"], REVIEW_STATUS_MISSING)
        self.assertEqual(live_lexicon["status"], REVIEW_STATUS_MISSING)
        self.assertEqual(snapshot["summary"][REVIEW_STATUS_MISSING], 2)

    def test_malformed_quality_report_becomes_failed_row(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            printout_dir = root / "printouts" / "lesson-d"
            printout_dir.mkdir(parents=True)
            (printout_dir / "material.docx").write_bytes(b"docx")
            (printout_dir / "quality_report.json").write_text("{bad-json", encoding="utf-8")
            settings = default_settings()
            settings["print_output_root"] = str(root / "printouts")

            snapshot = build_review_snapshot(settings)
            item = _first_item(snapshot, REVIEW_KIND_CLASS_PRINTOUT)

        self.assertEqual(item["status"], REVIEW_STATUS_FAILED)
        self.assertEqual(_metric(item, "issue"), "quality_report")


def _first_item(snapshot: dict[str, object], kind: str) -> dict[str, object]:
    for item in snapshot["items"]:  # type: ignore[index]
        if item["kind"] == kind:
            return item
    raise AssertionError(f"Missing review item: {kind}")


def _metric(item: dict[str, object], label: str) -> object:
    for metric in item["metrics"]:  # type: ignore[index]
        if metric["label"] == label:
            return metric["value"]
    raise AssertionError(f"Missing metric {label!r} in {item}")
