from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from wordbyword.gui.bridge import WordByWordBridge

FONT_ASSET_ROOT = Path(__file__).with_name("assets") / "fonts"
PRETENDARD_VARIABLE = FONT_ASSET_ROOT / "pretendard" / "PretendardVariable.ttf"
JETBRAINS_MONO_REGULAR = FONT_ASSET_ROOT / "jetbrains-mono" / "JetBrainsMono-Regular.ttf"
JETBRAINS_MONO_BOLD = FONT_ASSET_ROOT / "jetbrains-mono" / "JetBrainsMono-Bold.ttf"

INITIAL_UI_FONT_FAMILY = "Apple SD Gothic Neo"
UI_FONT_FALLBACKS = (
    "Pretendard Variable",
    "Pretendard",
    "Apple SD Gothic Neo",
    "Noto Sans KR",
    "Malgun Gothic",
    "Arial",
)
MONO_FONT_FALLBACKS = (
    "JetBrains Mono",
    "IBM Plex Mono",
    "Menlo",
    "monospace",
)


def load_gui_fonts() -> dict[str, str]:
    for font_path in (PRETENDARD_VARIABLE, JETBRAINS_MONO_REGULAR, JETBRAINS_MONO_BOLD):
        _register_font(font_path)
    return {
        "ui": _first_available_font(UI_FONT_FALLBACKS),
        "mono": _first_available_font(MONO_FONT_FALLBACKS),
    }


def configure_application_fonts(app: QApplication) -> dict[str, str]:
    font_families = load_gui_fonts()
    app.setFont(QFont(font_families["ui"]))
    return font_families


def main(argv: Sequence[str] | None = None) -> int:
    app_args = list(sys.argv if argv is None else argv)
    QApplication.setFont(QFont(INITIAL_UI_FONT_FAMILY))
    QQuickStyle.setStyle("Basic")
    app = QApplication(app_args)
    font_families = configure_application_fonts(app)
    engine = QQmlApplicationEngine()
    bridge = WordByWordBridge()
    bridge.setParent(app)
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.rootContext().setContextProperty("guiUiFontFamily", font_families["ui"])
    engine.rootContext().setContextProperty("guiMonoFontFamily", font_families["mono"])
    qml_path = Path(__file__).with_name("qml") / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        _release_qml_engine(engine)
        return 1
    exit_code = app.exec()
    _release_qml_engine(engine)
    return exit_code


def _release_qml_engine(engine: QQmlApplicationEngine) -> None:
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def _register_font(path: Path) -> None:
    if not path.is_file():
        return
    QFontDatabase.addApplicationFont(str(path.resolve()))


def _first_available_font(candidates: Sequence[str]) -> str:
    available = set(QFontDatabase.families())
    for family in candidates:
        if family in available:
            return family
    return candidates[-1]
