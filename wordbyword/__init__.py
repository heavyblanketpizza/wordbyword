"""Tools for extracting vocabulary candidates from documents."""

from wordbyword.llm import (
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
]
