"""Application command layer shared by CLI and GUI adapters."""

from wordbyword.application.commands import (
    CommandId,
    CommandResult,
    CommandSpec,
    build_cli_request,
    build_gui_request,
    command_id_from_cli_args,
    command_spec,
    registered_command_ids,
    run_cli_request,
    run_gui_request,
)

__all__ = [
    "CommandId",
    "CommandResult",
    "CommandSpec",
    "build_cli_request",
    "build_gui_request",
    "command_id_from_cli_args",
    "command_spec",
    "registered_command_ids",
    "run_cli_request",
    "run_gui_request",
]
