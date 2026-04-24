from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from kiwipiepy import Kiwi

from wordbyword.models import CountEntry
from wordbyword.text import clean_example_text

TEXT_RE = re.compile(r"[가-힣A-Za-z一-龥]")
LOW_VALUE_BOUND_NOUNS = {"것", "거", "수", "등", "데", "바", "뿐", "채", "척", "줄", "리", "만"}
PARTICLE_OR_ENDING_PREFIXES = ("J", "E")
FREQUENCY_RATE_SCALE = 10_000
IDEAL_FREQUENCY_RATE = 10.0
OVERUSED_FREQUENCY_RATE = 40.0
EXTREME_FREQUENCY_RATE = 100.0
EXTREME_FREQUENCY_PENALTY = -0.5
DEFAULT_ROI_SCORE = 0.5
POS_WEIGHTS = {
    "noun": 0.9,
    "verb": 0.72,
    "adjective": 0.7,
    "modifier_or_adverb": 0.55,
    "root": 0.86,
    "pronoun": 0.4,
    "proper_or_specific_noun": 0.45,
    "bound_noun": 0.2,
    "foreign_or_hanja": 0.86,
}
DEFAULT_POS_WEIGHT = 0.45

# TODO: Split candidate heuristics into smaller, tested pieces
# wordbyword/candidates.py is doing a lot: canonicalization, rejection, scoring, aggregation, dictionary packet generation. It is okay for now, but I would gradually split it into:
# canonicalize.py
# rejectors.py
# scoring.py
# candidates.py

@dataclass
class CandidateAggregate:
    canonical_key: str
    display: str
    kind: str
    count: int = 0
    documents: set[str] = field(default_factory=set)
    example: str = ""
    variants: Counter[str] = field(default_factory=Counter)
    pos_groups: Counter[str] = field(default_factory=Counter)
    surface_count: int = 0

    @property
    def document_count(self) -> int:
        return len(self.documents)

    def add_entry(
        self,
        *,
        variant: str,
        entry: CountEntry,
        amount: int | None = None,
        pos_group: str | None = None,
        surface_only: bool = False,
    ) -> None:
        count = amount if amount is not None else entry.count
        if surface_only:
            self.surface_count += count
        else:
            self.count += count

        self.documents.update(entry.documents)
        self.variants[variant] += count
        if pos_group:
            self.pos_groups[pos_group] += count
        if not self.example:
            self.example = entry.example


@dataclass(frozen=True)
class CandidateOutputs:
    terms: list[dict[str, object]]
    rejected: list[dict[str, object]]
    dictionary_entries: list[dict[str, object]]


def build_candidate_outputs(
    *,
    vocabulary: dict[tuple[str, str], CountEntry],
    surface_words: dict[str, CountEntry],
    total_document_count: int = 0,
    total_token_occurrences: int = 0,
    kiwi: Kiwi | None = None,
) -> CandidateOutputs:
    builder = CandidateBuilder(kiwi=kiwi)
    return builder.build(
        vocabulary=vocabulary,
        surface_words=surface_words,
        total_document_count=total_document_count,
        total_token_occurrences=total_token_occurrences,
    )


class CandidateBuilder:
    def __init__(self, kiwi: Kiwi | None = None) -> None:
        self.kiwi = kiwi if kiwi is not None else Kiwi()

    def build(
        self,
        *,
        vocabulary: dict[tuple[str, str], CountEntry],
        surface_words: dict[str, CountEntry],
        total_document_count: int = 0,
        total_token_occurrences: int = 0,
    ) -> CandidateOutputs:
        rejected: list[dict[str, object]] = []
        term_aggregates = self._build_term_aggregates(vocabulary, surface_words, rejected)
        term_rows = [
            term_row(
                aggregate,
                total_document_count=total_document_count,
                total_token_occurrences=total_token_occurrences,
            )
            for aggregate in term_aggregates.values()
        ]

        term_rows.sort(
            key=lambda row: (
                -float(row["roi_score"]),
                -float(row["quality_score"]),
                -int(row["count"]),
                str(row["canonical_key"]),
            )
        )

        dictionary_entries = [dictionary_entry(row) for row in term_rows]

        return CandidateOutputs(
            terms=term_rows,
            rejected=sorted(
                rejected,
                key=lambda row: (str(row["kind"]), str(row["reason"]), -int(row["count"]), str(row["candidate"])),
            ),
            dictionary_entries=dictionary_entries,
        )

    def _build_term_aggregates(
        self,
        vocabulary: dict[tuple[str, str], CountEntry],
        surface_words: dict[str, CountEntry],
        rejected: list[dict[str, object]],
    ) -> dict[str, CandidateAggregate]:
        aggregates: dict[str, CandidateAggregate] = {}

        for (term, pos), entry in sorted(vocabulary.items(), key=lambda item: (-item[1].count, item[0])):
            reason = reject_term(term, pos, entry)
            canonical_key = canonicalize_term(term, pos)
            if reason:
                rejected.append(rejected_row("term", term, canonical_key, reason, entry))
                continue

            aggregate = aggregates.setdefault(
                canonical_key,
                CandidateAggregate(
                    canonical_key=canonical_key,
                    display=canonical_key,
                    kind="term",
                ),
            )
            aggregate.add_entry(
                variant=term,
                entry=entry,
                pos_group=pos_group(pos),
            )

        for surface, entry in surface_words.items():
            variant = self._surface_variant(surface)
            if variant is None:
                continue

            canonical_key, display, group = variant
            aggregate = aggregates.setdefault(
                canonical_key,
                CandidateAggregate(
                    canonical_key=canonical_key,
                    display=display,
                    kind="term",
                ),
            )
            aggregate.add_entry(
                variant=surface,
                entry=entry,
                pos_group=group,
                surface_only=True,
            )

        return aggregates

    def _surface_variant(self, surface: str) -> tuple[str, str, str] | None:
        if not TEXT_RE.search(surface):
            return None

        tokens = self.kiwi.tokenize(surface)
        if not tokens:
            return None

        first = tokens[0]
        consumed = 1
        if first.tag.startswith("N") and len(tokens) > 1 and tokens[1].tag in {"XSV", "XSA"}:
            canonical_key = first.form + "하다"
            group = "verb" if tokens[1].tag == "XSV" else "adjective"
            consumed = 2
        elif first.tag.startswith(("VV", "VA")):
            canonical_key = canonicalize_term(first.form, first.tag)
            group = pos_group(first.tag)
        elif is_surface_head(first.tag):
            canonical_key = canonicalize_term(first.form, first.tag)
            group = pos_group(first.tag)
        else:
            return None

        if any(not is_allowed_variant_tail(token.tag) for token in tokens[consumed:]):
            return None

        return canonical_key, canonical_key, group


def reject_term(term: str, pos: str, entry: CountEntry) -> str | None:
    normalized = normalize_candidate_text(term)
    if not TEXT_RE.search(normalized):
        return "symbol_or_layout_only"
    if pos == "NNB" and normalized in LOW_VALUE_BOUND_NOUNS:
        return "low_value_bound_noun"
    return None


def canonicalize_term(term: str, pos: str) -> str:
    normalized = normalize_candidate_text(term)
    if pos.startswith(("VV", "VA")) and not normalized.endswith("다"):
        return normalized + "다"
    return normalized


def normalize_candidate_text(text: str) -> str:
    return clean_example_text(text)


def pos_group(pos: str) -> str:
    if pos.startswith("VV"):
        return "verb"
    if pos.startswith("VA"):
        return "adjective"
    if pos == "NNP":
        return "proper_or_specific_noun"
    if pos == "NNB":
        return "bound_noun"
    if pos.startswith("N"):
        return "noun"
    if pos == "NP":
        return "pronoun"
    if pos.startswith("M"):
        return "modifier_or_adverb"
    if pos == "XR":
        return "root"
    if pos in {"SL", "SH"}:
        return "foreign_or_hanja"
    return "other"


def is_surface_head(pos: str) -> bool:
    return pos.startswith(("N", "M", "XR", "SL", "SH"))


def is_allowed_variant_tail(pos: str) -> bool:
    return pos.startswith(PARTICLE_OR_ENDING_PREFIXES) or pos in {"XSN", "VCP", "VCN", "VX"}


def rejected_row(kind: str, candidate: str, canonical_key: str, reason: str, entry: CountEntry) -> dict[str, object]:
    return {
        "kind": kind,
        "candidate": candidate,
        "canonical_key": canonical_key,
        "reason": reason,
        "count": entry.count,
        "document_count": entry.document_count,
        "example": clean_example_text(entry.example),
    }


def term_row(
    aggregate: CandidateAggregate,
    *,
    total_document_count: int = 0,
    total_token_occurrences: int = 0,
) -> dict[str, object]:
    count = aggregate.count or aggregate.surface_count
    quality = term_quality_score(
        aggregate,
        total_document_count=total_document_count,
        total_token_occurrences=total_token_occurrences,
    )
    roi = roi_score()
    return {
        "canonical_key": aggregate.canonical_key,
        "display": aggregate.display,
        "kind": aggregate.kind,
        "pos_group": aggregate.pos_groups.most_common(1)[0][0] if aggregate.pos_groups else "unknown",
        "count": count,
        "surface_count": aggregate.surface_count,
        "document_count": aggregate.document_count,
        "variant_count": len(aggregate.variants),
        "variants": join_counter_keys(aggregate.variants),
        "quality_score": quality,
        "roi_score": roi,
        "example": clean_example_text(aggregate.example),
    }


def dictionary_entry(row: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "term",
        "canonical_key": row["canonical_key"],
        "display": row["display"],
        "variants": split_joined(row.get("variants", "")),
        "count": row["count"],
        "document_count": row["document_count"],
        "quality_score": row["quality_score"],
        "roi_score": row["roi_score"],
        "example": row["example"],
        "curation_status": "candidate",
    }


def term_quality_score(
    aggregate: CandidateAggregate,
    *,
    total_document_count: int = 0,
    total_token_occurrences: int = 0,
) -> float:
    count = aggregate.count or aggregate.surface_count
    frequency_count = max(count, aggregate.surface_count)
    frequency = frequency_usefulness_score(frequency_count, total_token_occurrences)
    dispersion = dispersion_score(aggregate.document_count, total_document_count)
    variant_signal = bounded_log_score(len(aggregate.variants), 20)
    pos = aggregate.pos_groups.most_common(1)[0][0] if aggregate.pos_groups else "unknown"
    pos_weight = POS_WEIGHTS.get(pos, DEFAULT_POS_WEIGHT)
    score = (0.42 * frequency) + (0.22 * dispersion) + (0.22 * pos_weight) + (0.14 * variant_signal)
    return round(clamp(score, 0.0, 1.0), 3)


def frequency_usefulness_score(count: int, total_token_occurrences: int) -> float:
    if count <= 0 or total_token_occurrences <= 0:
        return 0.0

    rate = count / total_token_occurrences * FREQUENCY_RATE_SCALE
    if rate <= IDEAL_FREQUENCY_RATE:
        return rate / IDEAL_FREQUENCY_RATE
    if rate <= OVERUSED_FREQUENCY_RATE:
        return 1.0 - ((rate - IDEAL_FREQUENCY_RATE) / (OVERUSED_FREQUENCY_RATE - IDEAL_FREQUENCY_RATE))
    if rate <= EXTREME_FREQUENCY_RATE:
        penalty_ratio = (rate - OVERUSED_FREQUENCY_RATE) / (EXTREME_FREQUENCY_RATE - OVERUSED_FREQUENCY_RATE)
        return EXTREME_FREQUENCY_PENALTY * penalty_ratio
    return EXTREME_FREQUENCY_PENALTY


def dispersion_score(document_count: int, total_document_count: int) -> float:
    if document_count <= 0:
        return 0.0

    denominator = total_document_count if total_document_count > 0 else document_count
    return min(document_count / denominator, 1.0)


# roi_score: Is this candidate worth prioritizing now for study, curation, or dictionary generation?
def roi_score() -> float:
    return DEFAULT_ROI_SCORE


def bounded_log_score(value: int, midpoint: int) -> float:
    if value <= 0:
        return 0.0
    return min(math.log1p(value) / math.log1p(midpoint), 1.0)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def join_counter_keys(counter: Counter[str]) -> str:
    return " | ".join(key for key, _ in counter.most_common(12))


def split_joined(value: object) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split("|") if part.strip()]
