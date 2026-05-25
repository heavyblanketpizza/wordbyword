from __future__ import annotations

import warnings
from typing import Any

from wordbyword.application.commands import CommandRequest


def run_cli_request(request: CommandRequest) -> int | None:
    warnings.warn(
        "wordbyword.application.use_cases.run_cli_request is deprecated; "
        "use wordbyword.cli.app.run_cli_request instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from wordbyword.cli.app import run_cli_request as adapter_run_cli_request

    return adapter_run_cli_request(request)


def run_gui_request(request: CommandRequest, emit: Any) -> Any:
    warnings.warn(
        "wordbyword.application.use_cases.run_gui_request is deprecated; "
        "use wordbyword.gui.workflows.run_gui_request instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from wordbyword.gui.workflows import run_gui_request as adapter_run_gui_request

    return adapter_run_gui_request(request, emit)
