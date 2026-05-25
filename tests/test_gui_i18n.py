from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path

from wordbyword.gui.i18n import I18N_ASSET_ROOT, catalog, language_state, normalize_language, text

REQUIRED_GUI_KEYS = {
    "language.label",
    "language.system",
    "language.english",
    "language.korean",
    "theme.label",
    "theme.standard",
    "theme.neobrutalist",
    "appearance.text_size_label",
    "appearance.text_size_value_suffix",
    "panel.end_to_end_flow",
    "panel.pipeline_stages",
    "panel.next_action",
    "status.ready",
    "status.missing_input",
    "status.done",
    "status.running",
    "status.failed",
    "workflow.before.title",
    "workflow.dictionary.title",
    "workflow.class.title",
    "workflow.archive.title",
    "workflow.reports.title",
    "readiness.dictionary.ready.label",
    "readiness.dictionary.ready.next",
    "button.refresh_status",
    "run.job_blocked",
    "run.no_artifacts",
    "run.settings_blocked",
}


class GuiI18nTests(unittest.TestCase):
    def test_english_and_korean_catalogs_have_matching_keys(self) -> None:
        english = _raw_catalog("en")
        korean = _raw_catalog("ko")

        self.assertEqual(set(english), set(korean))

    def test_korean_catalog_has_no_empty_required_values(self) -> None:
        korean = _raw_catalog("ko")

        for key in REQUIRED_GUI_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, korean)
                self.assertTrue(korean[key].strip())

    def test_code_referenced_translation_keys_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        english = _raw_catalog("en")
        referenced_keys: set[str] = set()
        for relative_path in ("wordbyword/gui/qml/Main.qml", "wordbyword/gui/pipeline.py"):
            source = (root / relative_path).read_text(encoding="utf-8")
            referenced_keys.update(re.findall(r'(?:root\.tr|(?<![A-Za-z0-9_.])t)\("([^"]+)"\)', source))

        for key in sorted(referenced_keys):
            with self.subTest(key=key):
                self.assertIn(key, english)

    def test_language_helpers_normalize_and_resolve_catalog_text(self) -> None:
        self.assertEqual(normalize_language("bogus"), "system")
        self.assertEqual(text("ko", "workflow.dictionary.title"), "사전 구축")

        state = language_state("ko")
        self.assertEqual(state["selected"], "ko")
        self.assertEqual(state["resolved"], "ko")
        self.assertIn({"value": "ko", "label": "한국어"}, state["options"])

    def test_public_catalog_uses_english_fallback_shape(self) -> None:
        english = catalog("en")
        korean = catalog("ko")

        self.assertEqual(set(english), set(korean))
        self.assertEqual(korean["workflow.before.title"], "시작 전 확인")

    def test_package_data_includes_gui_i18n_assets(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        package_data = pyproject["tool"]["setuptools"]["package-data"]["wordbyword.gui"]

        self.assertIn("assets/i18n/*.json", package_data)


def _raw_catalog(language: str) -> dict[str, str]:
    return json.loads((I18N_ASSET_ROOT / f"{language}.json").read_text(encoding="utf-8"))
