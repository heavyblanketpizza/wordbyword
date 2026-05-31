from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandEvent:
    workflow: str
    phase: str
    detail: str
    status: str = "running"
    current: int = 0
    total: int = 0
    artifacts: Mapping[str, str] = field(default_factory=dict)
    error: str = ""
    payload: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow": self.workflow,
            "phase": self.phase,
            "detail": self.detail,
            "status": self.status,
            "current": self.current,
            "total": self.total,
            "artifacts": dict(self.artifacts),
            "error": self.error,
            "payload": dict(self.payload),
        }


ProgressSink = Callable[[CommandEvent], None]


def event(
    workflow: str,
    phase: str,
    detail: str,
    *,
    status: str = "running",
    current: int = 0,
    total: int = 0,
    artifacts: Mapping[str, str] | None = None,
    error: str = "",
    payload: Mapping[str, object] | None = None,
) -> CommandEvent:
    return CommandEvent(
        workflow=workflow,
        phase=phase,
        detail=detail,
        status=status,
        current=current,
        total=total,
        artifacts={} if artifacts is None else artifacts,
        error=error,
        payload={} if payload is None else payload,
    )


def emit(sink: ProgressSink | None, command_event: CommandEvent) -> None:
    if sink is not None:
        sink(command_event)
