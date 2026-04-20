from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PageText:
    source: Path
    page: int | None
    text: str
    raw_text: str = ""


@dataclass
class CountEntry:
    count: int = 0
    documents: set[str] = field(default_factory=set)
    example: str = ""

    @property
    def document_count(self) -> int:
        return len(self.documents)

    def seen(self, source: str, example: str, amount: int = 1) -> None:
        self.count += amount
        self.documents.add(source)
        if not self.example:
            self.example = example
