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
        self.assertIn("summary", bridge.reviewSnapshot)
        self.assertIn("items", bridge.reviewSnapshot)
        self.assertEqual(bridge.languageState["selected"], "system")
        self.assertEqual(bridge.appearanceState["theme"], "standard")
        self.assertIn({"value": "neobrutalist", "label": "Neo Pop"}, bridge.appearanceState["theme_options"])
        self.assertEqual(bridge.appearanceState["theme_palette"]["app_bg"], "#f7f7f4")
        self.assertEqual(bridge.appearanceState["text_scale"], 1.0)
        self.assertEqual(bridge.appearanceState["text_scale_percent"], 100)

        self.assertTrue(bridge.setLanguage("ko"))
        self.assertEqual(bridge.languageState["selected"], "ko")
        self.assertEqual(bridge.texts["workflow.dictionary.title"], "사전 구축")
        self.assertIn({"value": "neobrutalist", "label": "컬러 네오팝"}, bridge.appearanceState["theme_options"])
        self.assertEqual(bridge.workflowDefinitions[1]["title"], "사전 구축")
        self.assertEqual(bridge.artifactText, "아직 산출물이 없습니다.")
        self.assertTrue(bridge.setTheme("neobrutalist"))
        self.assertEqual(bridge.appearanceState["theme"], "neobrutalist")
        self.assertEqual(bridge.appearanceState["theme_palette"]["primary_bg"], "#ffe15a")
        self.assertEqual(bridge.settings["gui_theme"], "neobrutalist")
        self.assertTrue(bridge.setTheme("bogus"))
        self.assertEqual(bridge.appearanceState["theme"], "standard")
        self.assertTrue(bridge.setTextScale(1.24))
        self.assertEqual(bridge.appearanceState["text_scale"], 1.25)
        self.assertEqual(bridge.settings["gui_text_scale"], 1.25)

    def test_bridge_rejects_external_workspace_guard_without_starting_worker(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.execution_guard import acquire_workspace_command_guard
        from wordbyword.gui.bridge import WordByWordBridge

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bridge = WordByWordBridge(settings_path=root / "settings.json", workspace_root=root)
            with acquire_workspace_command_guard("external cli", workspace_root=root):
                self.assertFalse(bridge.runJob("dictionary_build", {}))

            self.assertFalse(bridge.jobState["busy"])
            self.assertEqual(bridge.jobState["status"], "failed")
            self.assertEqual(bridge.jobState["phase"], "blocked")
            self.assertIn("external cli", bridge.logText)
            self.assertIsNone(bridge._thread)

    def test_bridge_rejects_second_job_without_overwriting_running_state(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.bridge import WordByWordBridge

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bridge = WordByWordBridge(settings_path=root / "settings.json", workspace_root=root)
            bridge._set_job_state(
                {
                    "busy": True,
                    "workflow": "class_printouts",
                    "phase": "rewrite",
                    "detail": "rewriting",
                    "status": "running",
                    "current": 1,
                    "total": 2,
                    "error": "",
                }
            )

            self.assertFalse(bridge.runJob("dictionary_build", {}))

            self.assertTrue(bridge.jobState["busy"])
            self.assertEqual(bridge.jobState["workflow"], "class_printouts")
            self.assertEqual(bridge.jobState["phase"], "rewrite")
            self.assertEqual(bridge.jobState["status"], "running")

    def test_bridge_releases_workspace_guard_when_job_finishes(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.execution_guard import acquire_workspace_command_guard
        from wordbyword.gui.bridge import WordByWordBridge

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bridge = WordByWordBridge(settings_path=root / "settings.json", workspace_root=root)
            bridge._job_guard = acquire_workspace_command_guard("gui job", workspace_root=root)

            bridge._on_finished({"status": "complete"})

            with acquire_workspace_command_guard("later job", workspace_root=root):
                pass

    def test_bridge_review_snapshot_refreshes_from_settings_and_finished_jobs(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.bridge import WordByWordBridge
        from wordbyword.gui.settings import default_settings, save_settings

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            settings_path = root / "settings.json"
            settings = default_settings()
            settings["print_output_root"] = str(root / "printouts")
            settings["dictionary_output_dir"] = str(root / "dictionary")
            settings["live_lexicon_path"] = str(root / "lexicon" / "canonical.jsonl")
            save_settings(settings, settings_path)
            bridge = WordByWordBridge(settings_path=settings_path, workspace_root=root)

            self.assertFalse(_review_items_of_kind(bridge.reviewSnapshot, "class_printout"))

            printout_dir = root / "new-printouts" / "lesson-a"
            printout_dir.mkdir(parents=True)
            (printout_dir / "material.docx").write_bytes(b"docx")
            (printout_dir / "quality_report.json").write_text(
                '{"source_id": "lesson-a", "status": "written"}',
                encoding="utf-8",
            )
            self.assertTrue(bridge.saveSettings({"print_output_root": str(root / "new-printouts")}))
            self.assertTrue(_review_items_of_kind(bridge.reviewSnapshot, "class_printout"))

            dictionary_dir = root / "dictionary"
            dictionary_dir.mkdir()
            (dictionary_dir / "15_final_dictionary.jsonl").write_text("{}\n", encoding="utf-8")
            bridge._on_finished({"status": "complete"})
            dictionary = _review_items_of_kind(bridge.reviewSnapshot, "dictionary")[0]
            self.assertEqual(dictionary["status"], "ready")

    def test_bridge_select_report_printout_updates_settings(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as error:
            self.skipTest(f"PySide6 is not installed: {error}")

        from wordbyword.gui.bridge import WordByWordBridge
        from wordbyword.gui.settings import default_settings, save_settings

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            handout = root / "archive" / "lesson.docx"
            handout.parent.mkdir()
            handout.write_bytes(b"docx")
            settings_path = root / "settings.json"
            settings = default_settings()
            settings["after_class_report_output_root"] = str(root / "reports")
            save_settings(settings, settings_path)
            bridge = WordByWordBridge(settings_path=settings_path, workspace_root=root)

            self.assertTrue(bridge.selectReportPrintout(str(handout)))

            self.assertEqual(bridge.settings["after_class_printout_path"], str(handout))
            self.assertEqual(bridge.workflowReadiness["reports"]["status"], "ready")

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
                for initial_scale in (0.9, 1.0, 1.3):
                    for initial_theme in ("standard", "neobrutalist"):
                        with self.subTest(
                            initial_language=initial_language,
                            initial_scale=initial_scale,
                            initial_theme=initial_theme,
                        ):
                            with tempfile.TemporaryDirectory() as workspace:
                                settings_path = Path(workspace) / "settings.json"
                                settings = default_settings()
                                settings["gui_language"] = initial_language
                                settings["gui_text_scale"] = initial_scale
                                settings["gui_theme"] = initial_theme
                                save_settings(settings, settings_path)
                                engine = QQmlApplicationEngine()
                                bridge = WordByWordBridge(settings_path=settings_path)
                                bridge.setParent(app)
                                engine.rootContext().setContextProperty("bridge", bridge)
                                engine.rootContext().setContextProperty("guiUiFontFamily", font_families["ui"])
                                engine.rootContext().setContextProperty("guiMonoFontFamily", font_families["mono"])
                                engine.load(QUrl.fromLocalFile(str(qml_path)))

                                self.assertTrue(engine.rootObjects())
                                root_object = engine.rootObjects()[0]
                                self.assertEqual(root_object.property("activePage"), "workflow")
                                self.assertEqual(root_object.property("settingsTab"), "appearance")
                                root_object.setProperty("activePage", "review")
                                self.assertEqual(root_object.property("activePage"), "review")
                                root_object.setProperty("activePage", "workflow")
                                saved_dictionary_input = str(Path(workspace) / "dictionary-source")

                                def switch_appearance_and_quit(
                                    bridge: WordByWordBridge = bridge,
                                    root_object: object = root_object,
                                    saved_dictionary_input: str = saved_dictionary_input,
                                ) -> None:
                                    root_object.setProperty("activePage", "settings")
                                    root_object.setProperty("settingsTab", "files_paths")
                                    bridge.saveSettings({"dictionary_input_dir": saved_dictionary_input})
                                    root_object.setProperty("settingsTab", "appearance")
                                    bridge.setLanguage("ko")
                                    bridge.setTheme("neobrutalist")
                                    bridge.setTextScale(1.3)
                                    root_object.setProperty("activePage", "workflow")
                                    root_object.setProperty("currentSection", 1)
                                    app.quit()

                                QTimer.singleShot(0, switch_appearance_and_quit)
                                app.exec()
                                self.assertEqual(root_object.property("activePage"), "workflow")
                                self.assertEqual(root_object.property("settingsTab"), "appearance")
                                self.assertEqual(root_object.property("currentSection"), 1)
                                self.assertEqual(bridge.languageState["selected"], "ko")
                                self.assertEqual(bridge.appearanceState["theme"], "neobrutalist")
                                self.assertEqual(bridge.appearanceState["text_scale"], 1.3)
                                self.assertEqual(bridge.settings["dictionary_input_dir"], saved_dictionary_input)
                                _release_qml_engine(engine)
        finally:
            qInstallMessageHandler(previous_handler)

        output = "\n".join(qt_messages)
        self.assertNotIn("Cannot read property", output)
        self.assertNotIn("of null", output)
        self.assertNotIn("Sans Serif", output)
        self.assertNotIn("Cannot open", output)
        self.assertNotIn(".svg", output)


def _review_items_of_kind(snapshot: dict[str, object], kind: str) -> list[dict[str, object]]:
    return [item for item in snapshot["items"] if item["kind"] == kind]  # type: ignore[index]
