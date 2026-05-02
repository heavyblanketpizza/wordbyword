from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from kiwipiepy import Kiwi

from wordbyword.dictionary_build.models import CountEntry, PageText
from wordbyword.dictionary_build.text import clean_example_text

PUNCTUATION_TAGS = {"SF", "SP", "SS", "SSO", "SSC", "SE", "SO", "SW", "SB"}
LEXICAL_PREFIXES = ("N", "V", "M", "XR", "SL", "SH")
EXCLUDED_VOCABULARY_TAGS = {"SN", "VCP", "VCN", "VX", "XPN", "XSN", "XSV", "XSA"}
SURFACE_WORD_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9一-龥]+(?:[.'’_/-][가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9一-龥]+)*")
SENTENCE_CONTENT_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9一-龥]")
SINGLE_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")


class KoreanAnalyzer:
    def __init__(
        self,
        *,
        include_punctuation: bool = False,
        include_numbers: bool = False,
        kiwi: Kiwi | None = None,
    ) -> None:
        self.kiwi = kiwi if kiwi is not None else Kiwi()
        self.include_punctuation = include_punctuation
        self.include_numbers = include_numbers

    def analyze_pages(self, pages: Iterable[PageText]) -> dict[str, object]:
        sentences: list[dict[str, object]] = []
        tokens: list[dict[str, object]] = []
        vocabulary: dict[tuple[str, str], CountEntry] = defaultdict(CountEntry)
        surface_words: dict[str, CountEntry] = defaultdict(CountEntry)
        document_totals: Counter[str] = Counter()

        for page in pages:
            source = page.document_id or page.source.name
            sentence_index = 0
            for sentence in self._split_sentences(page.text):
                sentence_text = clean_example_text(sentence)
                if is_noise_sentence(sentence_text):
                    continue

                sentence_index += 1
                sentences.append(
                    {
                        "document": source,
                        "page": page.page or "",
                        "sentence_index": sentence_index,
                        "sentence": sentence_text,
                    }
                )

                surface_forms = [
                    match.group(0)
                    for match in SURFACE_WORD_RE.finditer(sentence_text)
                    if self.include_numbers or not is_number_only(match.group(0))
                ]
                self._record_surface_words(surface_words, surface_forms, source, sentence_text)

                token_index = 0
                for token in self.kiwi.tokenize(sentence_text):
                    if not self.include_punctuation and token.tag in PUNCTUATION_TAGS:
                        continue

                    token_index += 1
                    token_row = {
                        "document": source,
                        "page": page.page or "",
                        "sentence_index": sentence_index,
                        "token_index": token_index,
                        "form": token.form,
                        "pos": token.tag,
                        "start": token.start,
                        "end": token.start + token.len,
                        "sentence": sentence_text,
                    }
                    tokens.append(token_row)
                    document_totals[source] += 1

                    if is_vocabulary_token(token.form, token.tag, include_numbers=self.include_numbers):
                        vocabulary[(token.form, token.tag)].seen(source, sentence_text)

        return {
            "sentences": sentences,
            "tokens": tokens,
            "vocabulary": vocabulary,
            "surface_words": surface_words,
            "document_totals": document_totals,
        }

    def _split_sentences(self, text: str) -> list[str]:
        if not text:
            return []

        try:
            sentences = self.kiwi.split_into_sents(text)
            if sentences:
                return [sentence.text for sentence in sentences]
        except Exception:
            pass

        return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if part.strip()]

    @staticmethod
    def _record_surface_words(
        surface_words: dict[str, CountEntry],
        forms: Iterable[str],
        source: str,
        sentence: str,
    ) -> None:
        for form in forms:
            surface_words[form].seen(source, sentence)

def is_vocabulary_token(form: str, pos: str, *, include_numbers: bool = False) -> bool:
    if not include_numbers and (pos == "SN" or is_number_only(form)):
        return False
    if pos in EXCLUDED_VOCABULARY_TAGS:
        return False
    return is_lexical_token(pos)


def is_lexical_token(pos: str) -> bool:
    return pos.startswith(LEXICAL_PREFIXES)


def is_number_only(value: str) -> bool:
    compact = re.sub(r"[\s,._/-]+", "", value)
    return bool(compact) and compact.isdigit()


def is_noise_sentence(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if is_number_only(text):
        return True
    if not SENTENCE_CONTENT_RE.search(text):
        return True
    return bool(SINGLE_HANGUL_RE.fullmatch(text))


def filter_entries_by_count(
    entries: dict[str, CountEntry] | dict[tuple[str, str], CountEntry],
    minimum: int,
) -> dict:
    return {key: value for key, value in entries.items() if value.count >= minimum}
