from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from wordbyword.gui.bridge import WordByWordBridge


def main(argv: Sequence[str] | None = None) -> int:
    app_args = list(sys.argv if argv is None else argv)
    QQuickStyle.setStyle("Basic")
    app = QApplication(app_args)
    engine = QQmlApplicationEngine()
    bridge = WordByWordBridge()
    engine.rootContext().setContextProperty("bridge", bridge)
    qml_path = Path(__file__).with_name("qml") / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 1
    return app.exec()
