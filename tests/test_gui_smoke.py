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
        self.assertEqual(bridge.languageState["selected"], "system")

        self.assertTrue(bridge.setLanguage("ko"))
        self.assertEqual(bridge.languageState["selected"], "ko")
        self.assertEqual(bridge.texts["workflow.dictionary.title"], "사전 구축")
        self.assertEqual(bridge.workflowDefinitions[1]["title"], "사전 구축")
        self.assertEqual(bridge.artifactText, "아직 산출물이 없습니다.")

    def test_redesigned_qml_loads_offscreen(self) -> None:
        try:
            from PySide6.QtCore import QUrl, QTimer, qInstallMessageHandler
            from PySide6.QtGui import QFont
            from PySide6.QtQml import QQmlApplicationEngine
            from PySide6.QtQuickControls2 import QQuickStyle
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.app import INITIAL_UI_FONT_FAMILY, _release_qml_engine, configure_application_fonts
        from wordbyword.gui.bridge import WordByWordBridge
        from wordbyword.gui.settings import default_settings, save_settings

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        qt_messages: list[str] = []

        def capture_qt_message(_mode: object, _context: object, message: str) -> None:
            qt_messages.append(message)

        previous_handler = qInstallMessageHandler(capture_qt_message)
        try:
            QApplication.setFont(QFont(INITIAL_UI_FONT_FAMILY))
            QQuickStyle.setStyle("Basic")
            app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
            font_families = configure_application_fonts(app)
            qml_path = Path(__file__).resolve().parents[1] / "wordbyword" / "gui" / "qml" / "Main.qml"

            for initial_language in ("system", "en", "ko"):
                with self.subTest(initial_language=initial_language):
                    with tempfile.TemporaryDirectory() as workspace:
                        settings_path = Path(workspace) / "settings.json"
                        settings = default_settings()
                        settings["gui_language"] = initial_language
                        save_settings(settings, settings_path)
                        engine = QQmlApplicationEngine()
                        bridge = WordByWordBridge(settings_path=settings_path)
                        bridge.setParent(app)
                        engine.rootContext().setContextProperty("bridge", bridge)
                        engine.rootContext().setContextProperty("guiUiFontFamily", font_families["ui"])
                        engine.rootContext().setContextProperty("guiMonoFontFamily", font_families["mono"])
                        engine.load(QUrl.fromLocalFile(str(qml_path)))

                        self.assertTrue(engine.rootObjects())
                        def switch_language_and_quit(bridge: WordByWordBridge = bridge) -> None:
                            bridge.setLanguage("ko")
                            app.quit()

                        QTimer.singleShot(0, switch_language_and_quit)
                        app.exec()
                        self.assertEqual(bridge.languageState["selected"], "ko")
                        _release_qml_engine(engine)
        finally:
            qInstallMessageHandler(previous_handler)

        output = "\n".join(qt_messages)
        self.assertNotIn("Cannot read property", output)
        self.assertNotIn("of null", output)
        self.assertNotIn("Sans Serif", output)
        self.assertNotIn("Cannot open", output)
        self.assertNotIn(".svg", output)
