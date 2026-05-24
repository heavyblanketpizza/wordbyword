from __future__ import annotations

import os
import tomllib
import unittest
from pathlib import Path

from wordbyword.gui.app import (
    FONT_ASSET_ROOT,
    JETBRAINS_MONO_BOLD,
    JETBRAINS_MONO_REGULAR,
    MONO_FONT_FALLBACKS,
    PRETENDARD_VARIABLE,
    UI_FONT_FALLBACKS,
)


class GuiFontTests(unittest.TestCase):
    def test_vendored_gui_font_assets_exist(self) -> None:
        expected_files = (
            PRETENDARD_VARIABLE,
            FONT_ASSET_ROOT / "pretendard" / "LICENSE-pretendard.txt",
            JETBRAINS_MONO_REGULAR,
            JETBRAINS_MONO_BOLD,
            FONT_ASSET_ROOT / "jetbrains-mono" / "LICENSE-jetbrains-mono.txt",
        )

        for path in expected_files:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)

    def test_package_data_includes_gui_font_assets(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        package_data = pyproject["tool"]["setuptools"]["package-data"]["wordbyword.gui"]

        self.assertIn("assets/fonts/pretendard/*.ttf", package_data)
        self.assertIn("assets/fonts/pretendard/*.txt", package_data)
        self.assertIn("assets/fonts/jetbrains-mono/*.ttf", package_data)
        self.assertIn("assets/fonts/jetbrains-mono/*.txt", package_data)

    def test_font_loader_reports_vendored_families(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.app import configure_application_fonts

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        font_families = configure_application_fonts(app)

        self.assertEqual(font_families["ui"], UI_FONT_FALLBACKS[0])
        self.assertEqual(font_families["mono"], MONO_FONT_FALLBACKS[0])
        self.assertEqual(app.font().family(), UI_FONT_FALLBACKS[0])

