"""Local GUI entry point for wordbyword."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from wordbyword.gui.app import main as gui_main

    return gui_main(argv)


__all__ = ["main"]
