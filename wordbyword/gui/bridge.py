from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QObject, Property, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from wordbyword.gui.job_events import error_event
from wordbyword.gui.pipeline import path_for_key, readiness_payload, workflow_definition_payload
from wordbyword.gui.settings import load_settings, save_settings
from wordbyword.gui.workflows import run_with_error_event


class JobWorker(QObject):
    event = Signal(object)
    finished = Signal(object)

    def __init__(self, workflow: str, values: Mapping[str, object]) -> None:
        super().__init__()
        self.workflow = workflow
        self.values = dict(values)

    @Slot()
    def run(self) -> None:
        def emit_event(event: object) -> None:
            self.event.emit(event.to_dict())

        try:
            final_event = run_with_error_event(self.workflow, self.values, emit_event)
        except Exception as error:  # pragma: no cover - defensive worker boundary
            final_event = error_event(self.workflow, "workflow crashed", error)
        payload = final_event.to_dict()
        self.event.emit(payload)
        self.finished.emit(payload)


class WordByWordBridge(QObject):
    settingsChanged = Signal()
    jobStateChanged = Signal()
    readinessChanged = Signal()
    logTextChanged = Signal()
    artifactTextChanged = Signal()

    def __init__(self, *, settings_path: Path | None = None) -> None:
        super().__init__()
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._job_state: dict[str, object] = {
            "busy": False,
            "workflow": "",
            "phase": "idle",
            "detail": "No job running.",
            "status": "idle",
            "current": 0,
            "total": 0,
            "error": "",
        }
        self._log_lines: list[str] = []
        self._artifacts: dict[str, str] = {}
        self._thread: QThread | None = None
        self._worker: JobWorker | None = None

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, object]:
        return dict(self._settings)

    @Property("QVariantList", constant=True)
    def workflowDefinitions(self) -> list[dict[str, object]]:
        return workflow_definition_payload()

    @Property("QVariantMap", notify=readinessChanged)
    def workflowReadiness(self) -> dict[str, dict[str, object]]:
        return readiness_payload(self._settings, job_state=self._job_state)

    @Property("QVariantMap", notify=jobStateChanged)
    def jobState(self) -> dict[str, object]:
        return dict(self._job_state)

    @Property(str, notify=logTextChanged)
    def logText(self) -> str:
        return "\n".join(self._log_lines[-200:])

    @Property(str, notify=artifactTextChanged)
    def artifactText(self) -> str:
        if not self._artifacts:
            return "No artifacts yet."
        return "\n".join(f"{key}: {value}" for key, value in sorted(self._artifacts.items()))

    @Slot("QVariantMap", result=bool)
    def saveSettings(self, values: Mapping[str, object]) -> bool:
        self._settings.update(_plain_mapping(values))
        save_settings(self._settings, self._settings_path)
        self.settingsChanged.emit()
        self.readinessChanged.emit()
        return True

    @Slot(str, "QVariantMap", result=bool)
    def runJob(self, workflow: str, values: Mapping[str, object]) -> bool:
        if bool(self._job_state.get("busy")):
            return False
        merged = dict(self._settings)
        merged.update(_plain_mapping(values))
        save_settings(merged, self._settings_path)
        self._settings = merged
        self.settingsChanged.emit()
        self.readinessChanged.emit()
        self._log_lines = []
        self._artifacts = {}
        self._set_job_state(
            {
                "busy": True,
                "workflow": workflow,
                "phase": "start",
                "detail": "Starting job.",
                "status": "running",
                "current": 0,
                "total": 0,
                "error": "",
            }
        )
        self.logTextChanged.emit()
        self.artifactTextChanged.emit()

        thread = QThread(self)
        worker = JobWorker(workflow, merged)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.event.connect(self._on_event)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker)
        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    @Slot(str, result=str)
    def chooseDirectory(self, current: str) -> str:
        selected = QFileDialog.getExistingDirectory(None, "Choose folder", current or str(Path.cwd()))
        return selected or current

    @Slot(str, result=str)
    def chooseFile(self, current: str) -> str:
        selected, _ = QFileDialog.getOpenFileName(None, "Choose file", current or str(Path.cwd()))
        return selected or current

    @Slot(str, result=bool)
    def openPath(self, path_text: str) -> bool:
        if not path_text:
            return False
        path = Path(path_text).expanduser()
        if not path.exists():
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    @Slot(str, result=str)
    def pathForKey(self, key: str) -> str:
        return path_for_key(self._settings, key)

    @Slot(object)
    def _on_event(self, payload: Mapping[str, object]) -> None:
        event = _plain_mapping(payload)
        artifacts = event.get("artifacts")
        if isinstance(artifacts, Mapping):
            self._artifacts.update({str(key): str(value) for key, value in artifacts.items()})
        status = str(event.get("status") or "running")
        line = f"[{status}] {event.get('workflow', '')}/{event.get('phase', '')}: {event.get('detail', '')}"
        error = str(event.get("error") or "")
        if error:
            line = f"{line} :: {error}"
        self._log_lines.append(line)
        self._set_job_state(
            {
                "busy": status == "running",
                "workflow": str(event.get("workflow") or ""),
                "phase": str(event.get("phase") or ""),
                "detail": str(event.get("detail") or ""),
                "status": status,
                "current": int(event.get("current") or 0),
                "total": int(event.get("total") or 0),
                "error": error,
            }
        )
        self.logTextChanged.emit()
        self.artifactTextChanged.emit()

    @Slot(object)
    def _on_finished(self, payload: Mapping[str, object]) -> None:
        event = _plain_mapping(payload)
        status = str(event.get("status") or "complete")
        self._set_job_state({**self._job_state, "busy": False, "status": status})

    @Slot()
    def _clear_worker(self) -> None:
        self._thread = None
        self._worker = None

    def _set_job_state(self, state: Mapping[str, object]) -> None:
        self._job_state = dict(state)
        self.jobStateChanged.emit()
        self.readinessChanged.emit()


def _plain_mapping(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = "" if value is None else value
        else:
            result[str(key)] = str(value)
    return result
