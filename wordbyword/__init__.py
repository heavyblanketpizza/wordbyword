"""Tools for extracting vocabulary candidates from documents."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "0.1.0"

_LAZY_EXPORTS = {
    "ChatMessage": "wordbyword.dictionary_build.llm",
    "CompletionResult": "wordbyword.dictionary_build.llm",
    "ProviderConfig": "wordbyword.dictionary_build.llm",
    "build_dictionary_entry": "wordbyword.dictionary_build.llm",
    "build_provider_client": "wordbyword.dictionary_build.llm",
    "claude_client": "wordbyword.dictionary_build.llm",
    "google_client": "wordbyword.dictionary_build.llm",
    "load_provider_config": "wordbyword.dictionary_build.llm",
    "ollama_client": "wordbyword.dictionary_build.llm",
    "openai_client": "wordbyword.dictionary_build.llm",
    "rewrite_sentence": "wordbyword.dictionary_build.llm",
    "build_context_packet": "wordbyword.classroom_printout_studio.class_rewriting.rewrite",
    "rewrite_whole_source_material": "wordbyword.classroom_printout_studio.class_rewriting.rewrite",
    "rewrite_whole_source_materials": "wordbyword.classroom_printout_studio.class_rewriting.rewrite",
}

__all__ = [*_LAZY_EXPORTS]


def __getattr__(name: str) -> Any:
    try:
        module_name = _LAZY_EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
