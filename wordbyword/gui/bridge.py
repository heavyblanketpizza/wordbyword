from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QObject, Property, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from wordbyword.execution_guard import (
    CommandAlreadyRunningError,
    WorkspaceCommandGuard,
    acquire_workspace_command_guard,
    active_command_summary,
)
from wordbyword.gui.i18n import catalog, language_state, normalize_language, resolve_language, text as localized_text
from wordbyword.gui.job_events import error_event
from wordbyword.gui.pipeline import path_for_key, readiness_payload, workflow_definition_payload
from wordbyword.gui.settings import (
    TEXT_SCALE_DEFAULT,
    TEXT_SCALE_MAX,
    TEXT_SCALE_MIN,
    TEXT_SCALE_STEP,
    load_settings,
    normalize_text_scale,
    save_settings,
    settings_with_target_grade_defaults,
)
from wordbyword.gui.themes import THEME_DEFAULT, normalize_theme, palette_for_payload, theme_options, theme_palette
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
    languageChanged = Signal()
    appearanceChanged = Signal()

    def __init__(self, *, settings_path: Path | None = None, workspace_root: Path | None = None) -> None:
        super().__init__()
        self._settings_path = settings_path
        self._workspace_root = (Path.cwd() if workspace_root is None else workspace_root).resolve()
        self._settings = load_settings(settings_path)
        self._job_state: dict[str, object] = {
            "busy": False,
            "workflow": "",
            "phase": "idle",
            "detail": self._text("run.no_job_running"),
            "status": "idle",
            "current": 0,
            "total": 0,
            "error": "",
        }
        self._log_lines: list[str] = []
        self._artifacts: dict[str, str] = {}
        self._thread: QThread | None = None
        self._worker: JobWorker | None = None
        self._job_guard: WorkspaceCommandGuard | None = None

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, object]:
        return dict(self._settings)

    @Property("QVariantMap", notify=languageChanged)
    def languageState(self) -> dict[str, object]:
        return language_state(self._selected_language())

    @Property("QVariantMap", notify=languageChanged)
    def texts(self) -> dict[str, str]:
        return catalog(self._resolved_language())

    @Property("QVariantMap", notify=appearanceChanged)
    def appearanceState(self) -> dict[str, object]:
        scale = self._text_scale()
        theme = self._theme()
        return {
            "text_scale": scale,
            "min_text_scale": TEXT_SCALE_MIN,
            "max_text_scale": TEXT_SCALE_MAX,
            "text_scale_step": TEXT_SCALE_STEP,
            "text_scale_percent": int(round(scale * 100)),
            "theme": theme,
            "theme_options": theme_options(self._resolved_language()),
            "theme_palette": palette_for_payload(theme_palette(theme)),
        }

    @Property("QVariantList", notify=languageChanged)
    def workflowDefinitions(self) -> list[dict[str, object]]:
        return workflow_definition_payload(language=self._resolved_language())

    @Property("QVariantMap", notify=readinessChanged)
    def workflowReadiness(self) -> dict[str, dict[str, object]]:
        return readiness_payload(self._settings, job_state=self._job_state, language=self._resolved_language())

    @Property("QVariantMap", notify=jobStateChanged)
    def jobState(self) -> dict[str, object]:
        return dict(self._job_state)

    @Property(str, notify=logTextChanged)
    def logText(self) -> str:
        return "\n".join(self._log_lines[-200:])

    @Property(str, notify=artifactTextChanged)
    def artifactText(self) -> str:
        if not self._artifacts:
            return self._text("run.no_artifacts")
        return "\n".join(f"{key}: {value}" for key, value in sorted(self._artifacts.items()))

    @Slot("QVariantMap", result=bool)
    def saveSettings(self, values: Mapping[str, object]) -> bool:
        if self._has_active_job():
            self._append_log_line(f"[blocked] settings/save: {self._text('run.settings_blocked')}")
            return False
        old_target_grade = self._settings.get("target_grade")
        self._settings.update(_plain_mapping(values))
        self._settings = settings_with_target_grade_defaults(self._settings, old_target_grade=old_target_grade)
        self._settings["gui_language"] = normalize_language(self._settings.get("gui_language"))
        self._settings["gui_theme"] = normalize_theme(self._settings.get("gui_theme"))
        self._settings["gui_text_scale"] = normalize_text_scale(self._settings.get("gui_text_scale"))
        save_settings(self._settings, self._settings_path)
        self.settingsChanged.emit()
        self.appearanceChanged.emit()
        self.readinessChanged.emit()
        return True

    @Slot(str, "QVariantMap", result=bool)
    def runJob(self, workflow: str, values: Mapping[str, object]) -> bool:
        if self._has_active_job():
            self._append_log_line(f"[blocked] {workflow}/start: {self._text('run.job_blocked')}")
            return False
        try:
            guard = acquire_workspace_command_guard(workflow, workspace_root=self._workspace_root)
        except CommandAlreadyRunningError as error:
            detail = self._blocked_job_detail(error)
            self._append_log_line(f"[blocked] {workflow}/start: {detail}")
            self._set_job_state(
                {
                    "busy": False,
                    "workflow": workflow,
                    "phase": "blocked",
                    "detail": detail,
                    "status": "failed",
                    "current": 0,
                    "total": 0,
                    "error": detail,
                }
            )
            return False
        merged = dict(self._settings)
        old_target_grade = merged.get("target_grade")
        try:
            merged.update(_plain_mapping(values))
            merged = settings_with_target_grade_defaults(merged, old_target_grade=old_target_grade)
            merged["gui_language"] = normalize_language(merged.get("gui_language"))
            merged["gui_theme"] = normalize_theme(merged.get("gui_theme"))
            merged["gui_text_scale"] = normalize_text_scale(merged.get("gui_text_scale"))
            save_settings(merged, self._settings_path)
            self._settings = merged
            self.settingsChanged.emit()
            self.appearanceChanged.emit()
            self.readinessChanged.emit()
            self._log_lines = []
            self._artifacts = {}
            self._set_job_state(
                {
                    "busy": True,
                    "workflow": workflow,
                    "phase": "start",
                    "detail": self._text("run.starting_job"),
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
            self._job_guard = guard
            thread.start()
        except Exception:
            if self._job_guard is guard:
                self._release_job_guard()
            else:
                guard.release()
            raise
        return True

    @Slot(str, result=str)
    def chooseDirectory(self, current: str) -> str:
        selected = QFileDialog.getExistingDirectory(None, self._text("dialog.choose_folder"), current or str(Path.cwd()))
        return selected or current

    @Slot(str, result=str)
    def chooseFile(self, current: str) -> str:
        selected, _ = QFileDialog.getOpenFileName(None, self._text("dialog.choose_file"), current or str(Path.cwd()))
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

    @Slot(str, result=bool)
    def setLanguage(self, value: str) -> bool:
        selected = normalize_language(value)
        if selected == self._selected_language():
            return True
        self._settings["gui_language"] = selected
        save_settings(self._settings, self._settings_path)
        if self._is_idle():
            self._job_state = {**self._job_state, "detail": self._text("run.no_job_running")}
            self.jobStateChanged.emit()
        self.settingsChanged.emit()
        self.languageChanged.emit()
        self.appearanceChanged.emit()
        self.readinessChanged.emit()
        self.artifactTextChanged.emit()
        return True

    @Slot(str, result=bool)
    def setTheme(self, value: str) -> bool:
        selected = normalize_theme(value)
        if selected == self._theme():
            return True
        self._settings["gui_theme"] = selected
        save_settings(self._settings, self._settings_path)
        self.settingsChanged.emit()
        self.appearanceChanged.emit()
        return True

    @Slot(float, result=bool)
    def setTextScale(self, value: float) -> bool:
        scale = normalize_text_scale(value)
        if scale == self._text_scale():
            return True
        self._settings["gui_text_scale"] = scale
        save_settings(self._settings, self._settings_path)
        self.settingsChanged.emit()
        self.appearanceChanged.emit()
        return True

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
        try:
            self._set_job_state({**self._job_state, "busy": False, "status": status})
        finally:
            self._release_job_guard()

    @Slot()
    def _clear_worker(self) -> None:
        self._release_job_guard()
        self._thread = None
        self._worker = None

    def _set_job_state(self, state: Mapping[str, object]) -> None:
        self._job_state = dict(state)
        self.jobStateChanged.emit()
        self.readinessChanged.emit()

    def _append_log_line(self, line: str) -> None:
        self._log_lines.append(line)
        self.logTextChanged.emit()

    def _release_job_guard(self) -> None:
        guard = self._job_guard
        self._job_guard = None
        if guard is not None:
            guard.release()

    def _blocked_job_detail(self, error: CommandAlreadyRunningError) -> str:
        return f"{self._text('run.job_blocked')} Active: {active_command_summary(error.active_command)}."

    def _selected_language(self) -> str:
        return normalize_language(self._settings.get("gui_language"))

    def _resolved_language(self) -> str:
        return resolve_language(self._selected_language())

    def _text(self, key: str) -> str:
        return localized_text(self._resolved_language(), key)

    def _text_scale(self) -> float:
        return normalize_text_scale(self._settings.get("gui_text_scale", TEXT_SCALE_DEFAULT))

    def _theme(self) -> str:
        return normalize_theme(self._settings.get("gui_theme", THEME_DEFAULT))

    def _is_idle(self) -> bool:
        return not bool(self._job_state.get("busy")) and str(self._job_state.get("status") or "") == "idle"

    def _has_active_job(self) -> bool:
        return bool(self._job_state.get("busy")) or self._job_guard is not None


def _plain_mapping(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = "" if value is None else value
        else:
            result[str(key)] = str(value)
    return result
