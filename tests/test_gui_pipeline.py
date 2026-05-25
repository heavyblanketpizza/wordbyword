from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wordbyword.gui.pipeline import (
    STATUS_DONE,
    STATUS_MISSING_INPUT,
    STATUS_READY,
    path_for_key,
    readiness_payload,
    workflow_definition_payload,
)
from wordbyword.gui.settings import default_settings

COMMON_GUI_ICONS = {
    "folder-open",
    "file-search",
    "play",
    "refresh-cw",
    "chevron-right",
    "chevron-down",
    "panel-bottom-open",
    "panel-bottom-close",
    "settings",
}


class GuiPipelineTests(unittest.TestCase):
    def test_workflow_definitions_follow_beginner_flow(self) -> None:
        definitions = workflow_definition_payload()

        self.assertEqual(
            [definition["key"] for definition in definitions],
            ["before_start", "dictionary", "class_printouts", "archive", "reports"],
        )
        dictionary = definitions[1]
        self.assertEqual(dictionary["primary_workflow_id"], "dictionary_build")
        self.assertGreaterEqual(len(dictionary["stages"]), 4)

    def test_workflow_definitions_include_icon_names(self) -> None:
        definitions = workflow_definition_payload()

        for definition in definitions:
            with self.subTest(workflow=definition["key"]):
                self.assertIsInstance(definition["icon_name"], str)
                self.assertTrue(definition["icon_name"])
                for stage in definition["stages"]:
                    self.assertIsInstance(stage["icon_name"], str)
                    self.assertTrue(stage["icon_name"])

    def test_workflow_definitions_can_be_localized_to_korean(self) -> None:
        definitions = workflow_definition_payload(language="ko")

        self.assertEqual(
            [definition["title"] for definition in definitions],
            ["시작 전 확인", "사전 구축", "수업 자료", "보관함 및 어휘 목록", "수업 후 보고서"],
        )
        self.assertEqual(definitions[1]["primary_workflow_id"], "dictionary_build")
        self.assertEqual(definitions[1]["stages"][0]["input_label"], "입력")

    def test_referenced_gui_icons_are_vendored_in_black_and_white(self) -> None:
        definitions = workflow_definition_payload()
        icon_names = set(COMMON_GUI_ICONS)
        for definition in definitions:
            icon_names.add(str(definition["icon_name"]))
            icon_names.update(str(stage["icon_name"]) for stage in definition["stages"])

        icon_root = Path(__file__).resolve().parents[1] / "wordbyword" / "gui" / "assets" / "icons"
        self.assertTrue((icon_root / "LICENSE-lucide.txt").is_file())
        for icon_name in sorted(icon_names):
            with self.subTest(icon=icon_name):
                black_icon = icon_root / "black" / f"{icon_name}.svg"
                white_icon = icon_root / "white" / f"{icon_name}.svg"
                self.assertTrue(black_icon.is_file())
                self.assertTrue(white_icon.is_file())
                self.assertIn('stroke="#111111"', black_icon.read_text(encoding="utf-8"))
                self.assertIn('stroke="#ffffff"', white_icon.read_text(encoding="utf-8"))

    def test_dictionary_readiness_reports_missing_source_folder(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            settings = default_settings()
            settings["dictionary_input_dir"] = str(Path(workspace) / "missing")
            settings["dictionary_output_dir"] = str(Path(workspace) / "build-dictionary")

            readiness = readiness_payload(settings)["dictionary"]

        self.assertEqual(readiness["status"], STATUS_MISSING_INPUT)
        self.assertIn("Dictionary source folder", readiness["missing"])

    def test_readiness_payload_can_be_localized_to_korean(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            settings = default_settings()
            settings["dictionary_input_dir"] = str(Path(workspace) / "missing")
            settings["dictionary_output_dir"] = str(Path(workspace) / "build-dictionary")

            readiness = readiness_payload(settings, language="ko")["dictionary"]

        self.assertEqual(readiness["status"], STATUS_MISSING_INPUT)
        self.assertEqual(readiness["label"], "원본 폴더 없음")
        self.assertIn("사전 원본 폴더", readiness["missing"])

    def test_dictionary_readiness_moves_from_ready_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "build-dictionary"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "lesson.txt").write_text("읽기 자료", encoding="utf-8")
            settings = default_settings()
            settings["dictionary_input_dir"] = str(input_dir)
            settings["dictionary_output_dir"] = str(output_dir)

            ready = readiness_payload(settings)["dictionary"]
            final_dictionary = output_dir / "15_final_dictionary.jsonl"
            final_dictionary.write_text("{}\n", encoding="utf-8")
            done = readiness_payload(settings)["dictionary"]

        self.assertEqual(ready["status"], STATUS_READY)
        self.assertEqual(done["status"], STATUS_DONE)
        self.assertEqual(done["artifacts"]["final_dictionary"], str(final_dictionary))

    def test_class_printouts_require_sources_and_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_dir = root / "class-source-materials"
            source_dir.mkdir()
            (source_dir / "lesson.pdf").write_bytes(b"%PDF placeholder")
            settings = default_settings()
            settings["class_source_dir"] = str(source_dir)
            settings["dictionary_output_dir"] = str(root / "build-dictionary")
            settings["live_lexicon_path"] = str(root / "missing-live.jsonl")
            settings["print_output_root"] = str(root / "printable-for-class")

            missing = readiness_payload(settings)["class_printouts"]
            dictionary_output = root / "build-dictionary"
            dictionary_output.mkdir()
            (dictionary_output / "15_final_dictionary.jsonl").write_text("{}\n", encoding="utf-8")
            ready = readiness_payload(settings)["class_printouts"]

        self.assertEqual(missing["status"], STATUS_MISSING_INPUT)
        self.assertIn("Live lexicon or final dictionary", missing["missing"])
        self.assertEqual(ready["status"], STATUS_READY)

    def test_path_for_key_resolves_virtual_dictionary_output(self) -> None:
        settings = default_settings()
        settings["dictionary_output_dir"] = "/tmp/wbw-build"

        self.assertEqual(path_for_key(settings, "dictionary_final"), "/tmp/wbw-build/15_final_dictionary.jsonl")
