"""Tools for extracting vocabulary candidates from documents."""

from wordbyword.dictionary_build.llm import (
    ChatMessage,
    CompletionResult,
    ProviderConfig,
    build_dictionary_entry,
    build_provider_client,
    claude_client,
    google_client,
    load_provider_config,
    ollama_client,
    openai_client,
    rewrite_sentence,
)
from wordbyword.class_rewriting.rewrite import (
    build_context_packet,
    rewrite_source_material,
    rewrite_source_materials,
    rewrite_whole_source_material,
    rewrite_whole_source_materials,
)

__version__ = "0.1.0"

__all__ = [
    "ChatMessage",
    "CompletionResult",
    "ProviderConfig",
    "build_dictionary_entry",
    "build_provider_client",
    "claude_client",
    "google_client",
    "load_provider_config",
    "ollama_client",
    "openai_client",
    "rewrite_sentence",
    "build_context_packet",
    "rewrite_source_material",
    "rewrite_source_materials",
    "rewrite_whole_source_material",
    "rewrite_whole_source_materials",
]
