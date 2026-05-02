from __future__ import annotations

import re

ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "\u200b\u200c\u200d\ufeff")
LAYOUT_MARKER_TRANSLATION = str.maketrans({"●": " ", "∨": " ", "«": ". "})
WHITESPACE_RE = re.compile(r"[\s\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")
EDGE_CURLY_QUOTES = "“”‘’"


def clean_layout_text(text: str) -> str:
    text = text.translate(ZERO_WIDTH_TRANSLATION)
    return text.translate(LAYOUT_MARKER_TRANSLATION)


def clean_example_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", clean_layout_text(text)).strip()


def strip_edge_curly_quotes(text: str) -> str:
    return text.strip(EDGE_CURLY_QUOTES)
