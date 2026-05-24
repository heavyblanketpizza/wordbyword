from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.gui.settings import SETTINGS_SCHEMA_VERSION, default_settings, load_settings, save_settings


class GuiSettingsTests(unittest.TestCase):
    def test_load_missing_settings_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "missing.json"

            settings = load_settings(path)

        self.assertEqual(settings["schema_version"], SETTINGS_SCHEMA_VERSION)
        self.assertEqual(settings["dictionary_output_dir"], default_settings()["dictionary_output_dir"])

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
