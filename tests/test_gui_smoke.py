from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class GuiSmokeTests(unittest.TestCase):
    def test_bridge_constructs_without_showing_window(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.bridge import WordByWordBridge

        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            bridge = WordByWordBridge(settings_path=Path(workspace) / "settings.json")

        self.assertFalse(bridge.jobState["busy"])
        self.assertIn("dictionary_output_dir", bridge.settings)
        self.assertEqual(bridge.workflowDefinitions[0]["key"], "before_start")
        self.assertIn("dictionary", bridge.workflowReadiness)

    def test_redesigned_qml_loads_offscreen(self) -> None:
        try:
            from PySide6.QtCore import QUrl, QTimer
            from PySide6.QtQml import QQmlApplicationEngine
            from PySide6.QtQuickControls2 import QQuickStyle
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.bridge import WordByWordBridge

        with tempfile.TemporaryDirectory() as workspace:
            QQuickStyle.setStyle("Basic")
            app = QApplication.instance() or QApplication([])
            engine = QQmlApplicationEngine()
            bridge = WordByWordBridge(settings_path=Path(workspace) / "settings.json")
            engine.rootContext().setContextProperty("bridge", bridge)
            qml_path = Path(__file__).resolve().parents[1] / "wordbyword" / "gui" / "qml" / "Main.qml"
            engine.load(QUrl.fromLocalFile(str(qml_path)))

            self.assertTrue(engine.rootObjects())
            QTimer.singleShot(0, app.quit)
