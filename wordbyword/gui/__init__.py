"""Native macOS interface for WordByWord."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from wordbyword.gui.launcher import main as launch

    return launch(argv)
