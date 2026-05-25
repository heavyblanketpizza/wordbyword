from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.gui.settings import (
    SETTINGS_SCHEMA_VERSION,
    TEXT_SCALE_DEFAULT,
    default_settings,
    load_settings,
    normalize_text_scale,
    save_settings,
)
from wordbyword.gui.themes import THEME_DEFAULT, normalize_theme


class GuiSettingsTests(unittest.TestCase):
    def test_load_missing_settings_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "missing.json"

            settings = load_settings(path)

        self.assertEqual(settings["schema_version"], SETTINGS_SCHEMA_VERSION)
        self.assertEqual(settings["dictionary_output_dir"], default_settings()["dictionary_output_dir"])
        self.assertEqual(settings["gui_language"], "system")
        self.assertEqual(settings["gui_theme"], THEME_DEFAULT)
        self.assertEqual(settings["gui_text_scale"], TEXT_SCALE_DEFAULT)

    def test_load_invalid_json_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "settings.json"
            path.write_text("{not-json", encoding="utf-8")

            settings = load_settings(path)

        self.assertEqual(settings["model"], default_settings()["model"])

    def test_save_and_load_settings_merges_known_values(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / ".wordbyword" / "gui-settings.json"

            save_settings(
                {
                    "model": "gemma-test",
                    "base_url": "http://localhost:11434",
                    "pdf_render_dpi": "180",
                    "unknown": "ignored",
                },
                path,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_settings(path)

        self.assertEqual(payload["schema_version"], SETTINGS_SCHEMA_VERSION)
        self.assertNotIn("unknown", payload)
        self.assertEqual(loaded["model"], "gemma-test")
        self.assertEqual(loaded["base_url"], "http://localhost:11434")
        self.assertEqual(loaded["pdf_render_dpi"], 180)

    def test_target_grade_updates_only_grade_default_paths(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / ".wordbyword" / "gui-settings.json"

            save_settings(
                {
                    "target_grade": "grade7",
                    "print_output_root": str(default_settings()["print_output_root"]),
                    "rewrite_output_root": str(Path(workspace) / "custom-rewrite"),
                },
                path,
            )
            loaded = load_settings(path)

        self.assertEqual(loaded["target_grade"], "grade7")
        self.assertEqual(
            loaded["print_output_root"],
            "classroom-printout-studio/printable-for-class/grade7-ko-a4-ollama",
        )
        self.assertEqual(loaded["rewrite_output_root"], str(Path(workspace) / "custom-rewrite"))
        self.assertEqual(
            loaded["live_lexicon_path"],
            "wbw-vocabulary-intelligence/live-grade-lexicon/grade7/canonical.jsonl",
        )
        self.assertEqual(loaded["dictionary_input_dir"], "build-dictionary/original/grade7")
        self.assertEqual(loaded["source_output_root"], "classroom-printout-studio/class-source-materials/parsed/grade7")

    def test_language_setting_is_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / ".wordbyword" / "gui-settings.json"

            save_settings({"gui_language": "ko"}, path)
            loaded = load_settings(path)

            path.write_text(json.dumps({"gui_language": "bogus"}), encoding="utf-8")
            invalid = load_settings(path)

        self.assertEqual(loaded["gui_language"], "ko")
        self.assertEqual(invalid["gui_language"], "system")

    def test_theme_setting_is_validated_and_persisted(self) -> None:
        self.assertEqual(normalize_theme("bogus"), THEME_DEFAULT)
        self.assertEqual(normalize_theme("NEOBRUTALIST"), "neobrutalist")

        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / ".wordbyword" / "gui-settings.json"

            save_settings({"gui_theme": "neobrutalist"}, path)
            loaded = load_settings(path)

            path.write_text(json.dumps({"gui_theme": "bogus"}), encoding="utf-8")
            invalid = load_settings(path)

        self.assertEqual(loaded["gui_theme"], "neobrutalist")
        self.assertEqual(invalid["gui_theme"], THEME_DEFAULT)

    def test_text_scale_setting_is_validated_clamped_and_snapped(self) -> None:
        self.assertEqual(normalize_text_scale("not-a-number"), 1.0)
        self.assertEqual(normalize_text_scale(0.1), 0.9)
        self.assertEqual(normalize_text_scale(2.0), 1.3)
        self.assertEqual(normalize_text_scale(1.03), 1.05)

        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / ".wordbyword" / "gui-settings.json"

            save_settings({"gui_text_scale": "1.24"}, path)
            loaded = load_settings(path)

            path.write_text(json.dumps({"gui_text_scale": "invalid"}), encoding="utf-8")
            invalid = load_settings(path)

        self.assertEqual(loaded["gui_text_scale"], 1.25)
        self.assertEqual(invalid["gui_text_scale"], TEXT_SCALE_DEFAULT)
