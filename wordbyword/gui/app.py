from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from wordbyword.gui.bridge import WordByWordBridge


def main(argv: Sequence[str] | None = None) -> int:
    app_args = list(sys.argv if argv is None else argv)
    QApplication.setFont(QFont("Arial"))
    QQuickStyle.setStyle("Basic")
    app = QApplication(app_args)
    app.setFont(QFont("Arial"))
    engine = QQmlApplicationEngine()
    bridge = WordByWordBridge()
    bridge.setParent(app)
    engine.rootContext().setContextProperty("bridge", bridge)
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
