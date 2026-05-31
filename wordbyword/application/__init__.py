"""Application command layer shared by CLI and GUI adapters."""

from wordbyword.application.commands import (
    CommandId,
    CommandResult,
    CommandSpec,
    command_spec,
    registered_command_ids,
)
from wordbyword.application.events import CommandEvent
from wordbyword.application.use_cases import run_request

__all__ = [
    "CommandId",
    "CommandEvent",
    "CommandResult",
    "CommandSpec",
    "command_spec",
    "registered_command_ids",
    "run_request",
]
