from __future__ import annotations

from collections.abc import Callable, Mapping

from wordbyword.application import use_cases as application_use_cases
from wordbyword.application.commands import CommandRequest
from wordbyword.application.events import CommandEvent
from wordbyword.gui.job_events import GuiJobEvent, error_event
from wordbyword.gui.request_builder import build_gui_request

EmitEvent = Callable[[GuiJobEvent], None]


def run_workflow(workflow: str, values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    return run_gui_request(build_gui_request(workflow, values), emit)


def run_gui_request(request: CommandRequest, emit: EmitEvent) -> GuiJobEvent:
    final_event: GuiJobEvent | None = None

    def sink(command_event: CommandEvent) -> None:
        nonlocal final_event
        gui_event = _gui_event(command_event)
        if command_event.phase == "complete":
            final_event = gui_event
            return
        emit(gui_event)

    result = application_use_cases.run_request(request, emit_event=sink)
    if final_event is not None:
        return final_event
    return GuiJobEvent(
        workflow=_workflow_from_result(request),
        phase="complete",
        detail=result.detail,
        status="complete" if result.exit_code == 0 else "failed",
        artifacts=result.artifacts,
        error=result.error,
    )


def run_with_error_event(workflow: str, values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    try:
        return run_workflow(workflow, values, emit)
    except Exception as error:
        return error_event(workflow, "workflow failed", error)


def _gui_event(command_event: CommandEvent) -> GuiJobEvent:
    return GuiJobEvent(
        workflow=command_event.workflow,
        phase=command_event.phase,
        detail=command_event.detail,
        status=command_event.status,
        current=command_event.current,
        total=command_event.total,
        artifacts=command_event.artifacts,
        error=command_event.error,
    )


def _workflow_from_result(request: CommandRequest) -> str:
    command_id = str(getattr(request, "command_id", ""))
    if "after_class_report" in command_id:
        return "reports"
    if "class_printouts" in command_id:
        return "class_printouts"
    if "archive" in command_id or "lexicon" in command_id or "usage" in command_id:
        return "archive"
    if "source_material" in command_id:
        return "source"
    return "dictionary"
