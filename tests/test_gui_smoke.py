from __future__ import annotations

import os
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

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            bridge = WordByWordBridge(settings_path=Path(workspace) / "settings.json")

        self.assertFalse(bridge.jobState["busy"])
        self.assertIn("dictionary_output_dir", bridge.settings)
        self.assertEqual(bridge.workflowDefinitions[0]["key"], "before_start")
        self.assertIn("dictionary", bridge.workflowReadiness)

    def test_redesigned_qml_loads_offscreen(self) -> None:
        try:
            from PySide6.QtCore import QUrl, QTimer, qInstallMessageHandler
            from PySide6.QtGui import QFont
            from PySide6.QtQml import QQmlApplicationEngine
            from PySide6.QtQuickControls2 import QQuickStyle
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.app import _release_qml_engine
        from wordbyword.gui.bridge import WordByWordBridge

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        qt_messages: list[str] = []

        def capture_qt_message(_mode: object, _context: object, message: str) -> None:
            qt_messages.append(message)

        previous_handler = qInstallMessageHandler(capture_qt_message)
        with tempfile.TemporaryDirectory() as workspace:
            try:
                QApplication.setFont(QFont("Arial"))
                QQuickStyle.setStyle("Basic")
                app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
                app.setFont(QFont("Arial"))
                engine = QQmlApplicationEngine()
                bridge = WordByWordBridge(settings_path=Path(workspace) / "settings.json")
                bridge.setParent(app)
                engine.rootContext().setContextProperty("bridge", bridge)
                qml_path = Path(__file__).resolve().parents[1] / "wordbyword" / "gui" / "qml" / "Main.qml"
                engine.load(QUrl.fromLocalFile(str(qml_path)))

                self.assertTrue(engine.rootObjects())
                QTimer.singleShot(0, app.quit)
                app.exec()
                _release_qml_engine(engine)
            finally:
                qInstallMessageHandler(previous_handler)

        output = "\n".join(qt_messages)
        self.assertNotIn("Cannot read property", output)
        self.assertNotIn("of null", output)
        self.assertNotIn("Sans Serif", output)
