"""Application command layer shared by CLI and GUI adapters."""

from typing import TYPE_CHECKING

from wordbyword.application.commands import (
    CommandId,
    CommandResult,
    CommandSpec,
    command_spec,
    registered_command_ids,
)
from wordbyword.application.events import CommandEvent

if TYPE_CHECKING:
    from wordbyword.application.use_cases import run_request


def __getattr__(name: str) -> object:
    if name == "run_request":
        from wordbyword.application.use_cases import run_request

        return run_request
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CommandId",
    "CommandEvent",
    "CommandResult",
    "CommandSpec",
    "command_spec",
    "registered_command_ids",
    "run_request",
]
