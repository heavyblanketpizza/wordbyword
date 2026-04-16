from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from kiwipiepy import Kiwi

from wordbyword.models import CountEntry
from wordbyword.text import clean_example_text

HANGUL_RE = re.compile(r"[가-힣]")
TEXT_RE = re.compile(r"[가-힣A-Za-z一-龥]")
LAYOUT_NUMBER_GLUE_RE = re.compile(r"(?:[가-힣][0-9]|[0-9][가-힣]|[.][0-9])")
LEADING_PARTICLE_RE = re.compile(r"^(?:은|는|이|가|을|를|의|에|에게|에서|께|한테|와|과|도|만|부터|까지|로|으로)(?:\s|$)")
FIGURE_LABEL_RE = re.compile(r"(?:^|\s)그림\s+[가-하](?:\s|$)")
TABLE_HEADER_FRAGMENT_RE = re.compile(r"(?:칭찬할\s+점|점\s+친구|친구\s+이름)")
LOW_VALUE_BOUND_NOUNS = {"것", "거", "수", "등", "데", "바", "뿐", "채", "척", "줄", "리", "만"}
PARTICLE_OR_ENDING_PREFIXES = ("J", "E")
NOUN_PREFIXES = ("N",)

# TODO: Split candidate heuristics into smaller, tested pieces
# wordbyword/candidates.py is doing a lot: canonicalization, rejection, scoring, aggregation, dictionary packet generation. It is okay for now, but I’d gradually split it into:
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
    first_source: str = ""
    first_page: int | None = None
    example: str = ""
    variants: Counter[str] = field(default_factory=Counter)
    pos_groups: Counter[str] = field(default_factory=Counter)
    content_terms: Counter[str] = field(default_factory=Counter)
    pos_patterns: Counter[str] = field(default_factory=Counter)
    sizes: Counter[int] = field(default_factory=Counter)
    noise_flags: Counter[str] = field(default_factory=Counter)
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
        content_terms: list[str] | None = None,
        pos_pattern: str | None = None,
        size: int | None = None,
        noise_flags: list[str] | None = None,
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
        if content_terms:
            self.content_terms.update(content_terms)
        if pos_pattern:
            self.pos_patterns[pos_pattern] += count
        if size is not None:
            self.sizes[size] += count
        if noise_flags:
            self.noise_flags.update(noise_flags)
        if not self.first_source:
            self.first_source = entry.first_source
            self.first_page = entry.first_page
            self.example = entry.example


@dataclass(frozen=True)
class CandidateOutputs:
    terms: list[dict[str, object]]
    expressions: list[dict[str, object]]
    rejected: list[dict[str, object]]
    dictionary_entries: list[dict[str, object]]


@dataclass(frozen=True)
class ExpressionFeatures:
    canonical_key: str
    content_terms: list[str]
    pos_pattern: str
    has_particle: bool
    has_verb_like: bool
    noun_only: bool
    noise_flags: list[str]


def build_candidate_outputs(
    *,
    vocabulary: dict[tuple[str, str], CountEntry],
    surface_words: dict[str, CountEntry],
    expressions: dict[tuple[str, int], CountEntry],
    min_expression_count: int,
) -> CandidateOutputs:
    builder = CandidateBuilder()
    return builder.build(
        vocabulary=vocabulary,
        surface_words=surface_words,
        expressions=expressions,
        min_expression_count=min_expression_count,
    )


class CandidateBuilder:
    def __init__(self) -> None:
        self.kiwi = Kiwi()

    def build(
        self,
        *,
        vocabulary: dict[tuple[str, str], CountEntry],
        surface_words: dict[str, CountEntry],
        expressions: dict[tuple[str, int], CountEntry],
        min_expression_count: int,
    ) -> CandidateOutputs:
        rejected: list[dict[str, object]] = []
        term_aggregates = self._build_term_aggregates(vocabulary, surface_words, rejected)
        expression_aggregates = self._build_expression_aggregates(expressions, min_expression_count, rejected)
        term_rows = [term_row(aggregate) for aggregate in term_aggregates.values()]
        expression_rows = [expression_row(aggregate) for aggregate in expression_aggregates.values()]

        term_rows.sort(key=lambda row: (-float(row["roi_score"]), -int(row["count"]), str(row["canonical_key"])))
        expression_rows.sort(key=lambda row: (-float(row["roi_score"]), -int(row["count"]), str(row["display"])))

        dictionary_entries = [
            dictionary_entry("term", row) for row in term_rows
        ] + [
            dictionary_entry("expression", row) for row in expression_rows
        ]

        return CandidateOutputs(
            terms=term_rows,
            expressions=expression_rows,
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

    def _build_expression_aggregates(
        self,
        expressions: dict[tuple[str, int], CountEntry],
        min_expression_count: int,
        rejected: list[dict[str, object]],
    ) -> dict[str, CandidateAggregate]:
        aggregates: dict[str, CandidateAggregate] = {}

        for (expression, size), entry in sorted(expressions.items(), key=lambda item: (-item[1].count, item[0][0])):
            features = self._expression_features(expression)
            reason = reject_expression(expression, entry, features, min_expression_count)
            if reason:
                rejected.append(rejected_row("expression", expression, features.canonical_key, reason, entry))
                continue

            aggregate = aggregates.setdefault(
                features.canonical_key,
                CandidateAggregate(
                    canonical_key=features.canonical_key,
                    display=expression,
                    kind="expression",
                ),
            )
            if entry.count > aggregate.variants[aggregate.display]:
                aggregate.display = expression
            aggregate.add_entry(
                variant=expression,
                entry=entry,
                content_terms=features.content_terms,
                pos_pattern=features.pos_pattern,
                size=size,
                noise_flags=features.noise_flags,
            )

        return aggregates

    def _expression_features(self, expression: str) -> ExpressionFeatures:
        tokens = self.kiwi.tokenize(expression)
        content_terms: list[str] = []
        pos_tags: list[str] = []
        has_particle = False
        has_verb_like = False
        nounish_count = 0
        noise_flags: list[str] = []

        if LAYOUT_NUMBER_GLUE_RE.search(expression):
            noise_flags.append("layout_number_glue")

        for token in tokens:
            pos_tags.append(token.tag)
            if token.tag.startswith("J"):
                has_particle = True
            if token.tag.startswith(("VV", "VA")):
                has_verb_like = True
                content_terms.append(canonicalize_term(token.form, token.tag))
                continue
            if token.tag in {"XSV", "XSA"}:
                has_verb_like = True
                if content_terms:
                    content_terms[-1] = content_terms[-1] + "하다"
                continue
            if token.tag.startswith(NOUN_PREFIXES):
                nounish_count += 1
            if is_expression_content_tag(token.tag):
                content_terms.append(canonicalize_term(token.form, token.tag))

        canonical_key = " ".join(content_terms) if content_terms else normalize_candidate_text(expression)
        noun_only = bool(content_terms) and nounish_count == len(content_terms) and not has_verb_like

        return ExpressionFeatures(
            canonical_key=canonical_key,
            content_terms=content_terms,
            pos_pattern=" ".join(pos_tags),
            has_particle=has_particle,
            has_verb_like=has_verb_like,
            noun_only=noun_only,
            noise_flags=noise_flags,
        )


def reject_term(term: str, pos: str, entry: CountEntry) -> str | None:
    normalized = normalize_candidate_text(term)
    if not TEXT_RE.search(normalized):
        return "symbol_or_layout_only"
    if pos == "NNB" and normalized in LOW_VALUE_BOUND_NOUNS:
        return "low_value_bound_noun"
    if len(normalized) == 1 and entry.count == 1 and pos not in {"NP", "MAG"}:
        return "single_character_low_frequency"
    return None


def reject_expression(
    expression: str,
    entry: CountEntry,
    features: ExpressionFeatures,
    min_expression_count: int,
) -> str | None:
    if not HANGUL_RE.search(expression):
        return "no_hangul"
    if LEADING_PARTICLE_RE.search(expression) or features.content_terms[:1] in (["르"], ["을"], ["를"]):
        return "leading_particle_fragment"
    if FIGURE_LABEL_RE.search(expression):
        return "figure_label_fragment"
    if TABLE_HEADER_FRAGMENT_RE.search(expression):
        return "table_header_fragment"
    if "layout_number_glue" in features.noise_flags:
        return "layout_number_glue"
    if len(features.content_terms) < 2:
        return "too_little_content"
    if entry.count < min_expression_count:
        return "below_frequency_floor"
    if features.noun_only and not features.has_particle and entry.count == 1 and len(features.content_terms) >= 3:
        return "low_confidence_noun_list"
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


def is_expression_content_tag(pos: str) -> bool:
    if pos in {"SN", "VCP", "VCN", "VX", "XPN", "XSN"}:
        return False
    return pos.startswith(("N", "M", "XR", "SL", "SH"))


def rejected_row(kind: str, candidate: str, canonical_key: str, reason: str, entry: CountEntry) -> dict[str, object]:
    return {
        "kind": kind,
        "candidate": candidate,
        "canonical_key": canonical_key,
        "reason": reason,
        "count": entry.count,
        "document_count": entry.document_count,
        "first_source": entry.first_source,
        "first_page": entry.first_page or "",
        "example": clean_example_text(entry.example),
    }


def term_row(aggregate: CandidateAggregate) -> dict[str, object]:
    count = aggregate.count or aggregate.surface_count
    quality = term_quality_score(aggregate)
    roi = roi_score(quality, aggregate.document_count, count)
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
        "first_source": aggregate.first_source,
        "first_page": aggregate.first_page or "",
        "example": clean_example_text(aggregate.example),
    }


def expression_row(aggregate: CandidateAggregate) -> dict[str, object]:
    quality = expression_quality_score(aggregate)
    roi = roi_score(quality, aggregate.document_count, aggregate.count)
    return {
        "canonical_key": aggregate.canonical_key,
        "display": aggregate.display,
        "kind": aggregate.kind,
        "size_min": min(aggregate.sizes) if aggregate.sizes else "",
        "size_max": max(aggregate.sizes) if aggregate.sizes else "",
        "count": aggregate.count,
        "document_count": aggregate.document_count,
        "variant_count": len(aggregate.variants),
        "variants": join_counter_keys(aggregate.variants),
        "content_terms": join_counter_keys(aggregate.content_terms),
        "pos_pattern": aggregate.pos_patterns.most_common(1)[0][0] if aggregate.pos_patterns else "",
        "noise_flags": join_counter_keys(aggregate.noise_flags),
        "quality_score": quality,
        "roi_score": roi,
        "first_source": aggregate.first_source,
        "first_page": aggregate.first_page or "",
        "example": clean_example_text(aggregate.example),
    }


def dictionary_entry(kind: str, row: dict[str, object]) -> dict[str, object]:
    return {
        "kind": kind,
        "canonical_key": row["canonical_key"],
        "display": row["display"],
        "variants": split_joined(row.get("variants", "")),
        "count": row["count"],
        "document_count": row["document_count"],
        "quality_score": row["quality_score"],
        "roi_score": row["roi_score"],
        "first_source": row["first_source"],
        "first_page": row["first_page"],
        "example": row["example"],
        "curation_status": "candidate",
    }


def term_quality_score(aggregate: CandidateAggregate) -> float:
    count = aggregate.count or aggregate.surface_count
    frequency = bounded_log_score(count, 1000)
    dispersion = min(aggregate.document_count / 2, 1.0)
    variant_signal = bounded_log_score(len(aggregate.variants), 20)
    pos = aggregate.pos_groups.most_common(1)[0][0] if aggregate.pos_groups else "unknown"
    pos_weight = {
        "noun": 0.92,
        "verb": 0.9,
        "adjective": 0.88,
        "modifier_or_adverb": 0.8,
        "root": 0.74,
        "pronoun": 0.65,
        "proper_or_specific_noun": 0.58,
        "bound_noun": 0.35,
        "foreign_or_hanja": 0.45,
    }.get(pos, 0.5)
    return round((0.42 * frequency) + (0.22 * dispersion) + (0.22 * pos_weight) + (0.14 * variant_signal), 3)


def expression_quality_score(aggregate: CandidateAggregate) -> float:
    frequency = bounded_log_score(aggregate.count, 100)
    dispersion = min(aggregate.document_count / 2, 1.0)
    variant_signal = bounded_log_score(len(aggregate.variants), 12)
    content_len = len(aggregate.content_terms)
    content_score = min(content_len / 4, 1.0)
    noise_penalty = min(sum(aggregate.noise_flags.values()) * 0.08, 0.3)
    score = (0.38 * frequency) + (0.22 * dispersion) + (0.2 * variant_signal) + (0.2 * content_score) - noise_penalty
    return round(max(score, 0.0), 3)


def roi_score(quality: float, document_count: int, count: int) -> float:
    transfer = 0.35 + min(document_count / 4, 0.25) + min(math.log1p(count) / math.log1p(200), 0.3)
    return round(min(quality * transfer, 1.0), 3)


def bounded_log_score(value: int, midpoint: int) -> float:
    if value <= 0:
        return 0.0
    return min(math.log1p(value) / math.log1p(midpoint), 1.0)


def join_counter_keys(counter: Counter[str]) -> str:
    return " | ".join(key for key, _ in counter.most_common(12))


def split_joined(value: object) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split("|") if part.strip()]
