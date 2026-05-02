from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from wordbyword.dictionary_build.candidates import canonicalize_term, is_surface_head
from wordbyword.dictionary_build.text import clean_example_text

COVERED_CANONICAL_KEY_FIELDS = (
    "cleaned_covered_canonical_keys",
    "covered_canonical_keys",
    "source_covered_canonical_keys",
)


def sentence_candidate_keys(
    token_rows: Sequence[Mapping[str, object]],
    known_keys: Collection[str],
) -> list[str]:
    matched_keys: list[str] = []
    index = 0
    while index < len(token_rows):
        token = token_rows[index]
        form = str(token.get("form", ""))
        pos = str(token.get("pos", ""))

        if allows_hada_merge(pos) and index + 1 < len(token_rows):
            next_pos = str(token_rows[index + 1].get("pos", ""))
            if next_pos in {"XSV", "XSA"}:
                canonical_key = form + "하다"
                if canonical_key in known_keys:
                    matched_keys.append(canonical_key)
                    index += 2
                    continue

        canonical_key = token_canonical_key(form, pos)
        if canonical_key in known_keys:
            matched_keys.append(canonical_key)
        index += 1

    return matched_keys


def token_canonical_key(form: str, pos: str) -> str | None:
    if pos.startswith(("VV", "VA")) or is_surface_head(pos):
        return canonicalize_term(form, pos)
    return None


def allows_hada_merge(pos: str) -> bool:
    return pos.startswith("N") or pos == "XR"


def ordered_unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def join_keys(values: Sequence[str]) -> str:
    return " | ".join(values)


def split_joined_keys(value: object) -> list[str]:
    text = clean_example_text(str(value))
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def row_covered_canonical_keys(
    row: Mapping[str, object],
    *,
    field_names: Sequence[str] = COVERED_CANONICAL_KEY_FIELDS,
) -> list[str]:
    for field_name in field_names:
        values = split_joined_keys(row.get(field_name, ""))
        if values:
            return values
    return []
