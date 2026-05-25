from __future__ import annotations

from collections.abc import Mapping

from wordbyword.gui.i18n import text as localized_text

THEME_STANDARD = "standard"
THEME_NEOBRUTALIST = "neobrutalist"
THEME_DEFAULT = THEME_STANDARD
THEME_VALUES = {THEME_STANDARD, THEME_NEOBRUTALIST}

STANDARD_PALETTE: dict[str, object] = {
    "ink": "#111111",
    "ink_soft": "#333333",
    "text_muted": "#555555",
    "text_subtle": "#666666",
    "app_bg": "#f7f7f4",
    "sidebar_bg": "#ffffff",
    "panel_bg": "#ffffff",
    "muted_bg": "#f7f7f4",
    "control_bg": "#ffffff",
    "disabled_bg": "#e7e7e2",
    "primary_bg": "#111111",
    "primary_ink": "#ffffff",
    "active_bg": "#111111",
    "active_ink": "#ffffff",
    "progress_bg": "#f7f7f4",
    "progress_fill": "#111111",
    "status_ready_bg": "#f7f7f4",
    "status_done_bg": "#111111",
    "status_running_bg": "#333333",
    "status_problem_bg": "#ffffff",
    "runtime_bg": "#f7f7f4",
    "stage_marker_bg": "#f7f7f4",
    "workflow_accents": ["#f7f7f4", "#f7f7f4", "#f7f7f4", "#f7f7f4", "#f7f7f4"],
}

NEOBRUTALIST_PALETTE: dict[str, object] = {
    "ink": "#111111",
    "ink_soft": "#111111",
    "text_muted": "#3b3430",
    "text_subtle": "#514842",
    "app_bg": "#f3e7ff",
    "sidebar_bg": "#fff0a8",
    "panel_bg": "#fffaf0",
    "muted_bg": "#ffffff",
    "control_bg": "#ffffff",
    "disabled_bg": "#f1ede8",
    "primary_bg": "#ffe15a",
    "primary_ink": "#111111",
    "active_bg": "#ff7a66",
    "active_ink": "#111111",
    "progress_bg": "#ffffff",
    "progress_fill": "#ff7a66",
    "status_ready_bg": "#fffaf0",
    "status_done_bg": "#b8ff9f",
    "status_running_bg": "#ffe15a",
    "status_problem_bg": "#ffb4e6",
    "runtime_bg": "#c7b8ff",
    "stage_marker_bg": "#ffffff",
    "workflow_accents": ["#ffe15a", "#b8ff9f", "#9ee7ff", "#c7b8ff", "#ffb4e6"],
}


def normalize_theme(value: object) -> str:
    theme = str(value or "").strip().lower()
    if theme in THEME_VALUES:
        return theme
    return THEME_DEFAULT


def theme_palette(theme: object) -> dict[str, object]:
    selected = normalize_theme(theme)
    if selected == THEME_NEOBRUTALIST:
        return dict(NEOBRUTALIST_PALETTE)
    return dict(STANDARD_PALETTE)


def theme_options(language: str) -> list[dict[str, str]]:
    return [
        {"value": THEME_STANDARD, "label": localized_text(language, "theme.standard")},
        {"value": THEME_NEOBRUTALIST, "label": localized_text(language, "theme.neobrutalist")},
    ]


def palette_for_payload(palette: Mapping[str, object]) -> dict[str, object]:
    return {str(key): value for key, value in palette.items()}
