from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence
from urllib import error, parse, request

from wordbyword.text import clean_example_text

ProviderName = Literal["openai", "claude", "google", "ollama"]
MessageRole = Literal["system", "user", "assistant"]

DEFAULT_PROVIDER_MODELS: dict[ProviderName, str] = {
    "openai": "gpt-4o",
    "claude": "claude-3-5-sonnet-20240620",
    "google": "gemini-1.5-pro",
    "ollama": "llama3.1",
}

DEFAULT_PROVIDER_BASE_URLS: dict[ProviderName, str] = {
    "openai": "https://api.openai.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "ollama": "http://localhost:11434",
}

SENTENCE_REWRITE_SYSTEM_PROMPT = """You rewrite Korean example sentences for learners.
Keep the meaning faithful.
Prefer natural Korean over literal wording.
If focus terms are supplied, preserve them when it still sounds natural.
Return only the rewritten sentence."""

DICTIONARY_ENTRY_SYSTEM_PROMPT = """You turn Korean vocabulary candidate packets into learner-friendly dictionary entries.
Return exactly one JSON object.
Required keys:
- canonical_key: string
- display: string
- part_of_speech: string
- english_glosses: array of short strings
- learner_note: string
- rewritten_examples: array of short Korean example sentences
- confidence: number from 0.0 to 1.0
Do not include markdown fences or commentary."""


class LLMConfigurationError(ValueError):
    """Raised when a provider config is incomplete."""


class LLMRequestError(RuntimeError):
    """Raised when a provider request fails."""


class LLMResponseError(RuntimeError):
    """Raised when a provider response is malformed."""


@dataclass(frozen=True)
class ChatMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ProviderConfig:
    provider: ProviderName
    model: str
    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 60.0
    default_temperature: float = 0.7
    max_output_tokens: int = 1024


@dataclass(frozen=True)
class CompletionResult:
    provider: ProviderName
    model: str
    content: str
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None


class JsonTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class SupportsCompletion(Protocol):
    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult: ...


class UrlLibJsonTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **headers}
        http_request = request.Request(url, data=payload, headers=request_headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = read_error_body(exc)
            raise LLMRequestError(f"Request to {url} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise LLMRequestError(f"Request to {url} failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Provider at {url} returned invalid JSON.") from exc


class BaseChatClient:
    def __init__(self, config: ProviderConfig, transport: JsonTransport | None = None) -> None:
        self.config = config
        self.transport = transport if transport is not None else UrlLibJsonTransport()

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        resolved_temperature = self.config.default_temperature if temperature is None else temperature
        resolved_max_tokens = self.config.max_output_tokens if max_output_tokens is None else max_output_tokens
        response = self.transport.post_json(
            url=self.endpoint_url(messages, json_mode=json_mode),
            headers=self.headers(),
            body=self.request_body(
                messages,
                temperature=resolved_temperature,
                max_output_tokens=resolved_max_tokens,
                json_mode=json_mode,
            ),
            timeout_seconds=self.config.timeout_seconds,
        )
        return self.parse_response(response)

    def endpoint_url(self, messages: Sequence[ChatMessage], *, json_mode: bool) -> str:
        raise NotImplementedError

    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    def request_body(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_output_tokens: int,
        json_mode: bool,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_response(self, payload: Mapping[str, Any]) -> CompletionResult:
        raise NotImplementedError


class OpenAIChatClient(BaseChatClient):
    def endpoint_url(self, messages: Sequence[ChatMessage], *, json_mode: bool) -> str:
        return f"{self.config.base_url}/chat/completions"

    def headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise LLMConfigurationError("OpenAI requires OPENAI_API_KEY.")
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def request_body(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_output_tokens: int,
        json_mode: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [message_row(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def parse_response(self, payload: Mapping[str, Any]) -> CompletionResult:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("OpenAI response did not include any choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise LLMResponseError("OpenAI returned an invalid choice object.")
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise LLMResponseError("OpenAI response did not include a message object.")
        return CompletionResult(
            provider="openai",
            model=self.config.model,
            content=content_text(message.get("content")),
            finish_reason=string_or_none(first_choice.get("finish_reason")),
            raw=dict(payload),
        )


class ClaudeChatClient(BaseChatClient):
    def endpoint_url(self, messages: Sequence[ChatMessage], *, json_mode: bool) -> str:
        return f"{self.config.base_url}/messages"

    def headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise LLMConfigurationError("Claude requires ANTHROPIC_API_KEY.")
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }

    def request_body(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_output_tokens: int,
        json_mode: bool,
    ) -> dict[str, Any]:
        system_prompt, conversation = split_system_messages(messages)
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": anthropic_messages(conversation),
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if system_prompt:
            body["system"] = system_prompt
        return body

    def parse_response(self, payload: Mapping[str, Any]) -> CompletionResult:
        content = payload.get("content")
        if not isinstance(content, list) or not content:
            raise LLMResponseError("Claude response did not include any content blocks.")
        return CompletionResult(
            provider="claude",
            model=self.config.model,
            content=content_text(content),
            finish_reason=string_or_none(payload.get("stop_reason")),
            raw=dict(payload),
        )


class GoogleChatClient(BaseChatClient):
    def endpoint_url(self, messages: Sequence[ChatMessage], *, json_mode: bool) -> str:
        if not self.config.api_key:
            raise LLMConfigurationError("Google requires GOOGLE_API_KEY or GEMINI_API_KEY.")
        encoded_model = parse.quote(self.config.model, safe="")
        encoded_key = parse.quote(self.config.api_key, safe="")
        return f"{self.config.base_url}/models/{encoded_model}:generateContent?key={encoded_key}"

    def headers(self) -> dict[str, str]:
        return {}

    def request_body(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_output_tokens: int,
        json_mode: bool,
    ) -> dict[str, Any]:
        system_prompt, conversation = split_system_messages(messages)
        body: dict[str, Any] = {
            "contents": google_messages(conversation),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        return body

    def parse_response(self, payload: Mapping[str, Any]) -> CompletionResult:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise LLMResponseError("Google response did not include any candidates.")
        first = candidates[0]
        if not isinstance(first, Mapping):
            raise LLMResponseError("Google returned an invalid candidate object.")
        content = first.get("content")
        if not isinstance(content, Mapping):
            raise LLMResponseError("Google response did not include a content object.")
        return CompletionResult(
            provider="google",
            model=self.config.model,
            content=content_text(content.get("parts")),
            finish_reason=string_or_none(first.get("finishReason")),
            raw=dict(payload),
        )


class OllamaChatClient(BaseChatClient):
    def endpoint_url(self, messages: Sequence[ChatMessage], *, json_mode: bool) -> str:
        return f"{self.config.base_url}/api/chat"

    def headers(self) -> dict[str, str]:
        return {}

    def request_body(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_output_tokens: int,
        json_mode: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [message_row(message) for message in messages],
            "stream": False,
            # The current helper layer expects final answer text in message.content.
            # Disable Ollama reasoning traces by default so thinking-capable models
            # do not consume the token budget in message.thinking instead.
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_output_tokens,
            },
        }
        if json_mode:
            body["format"] = "json"
        return body

    def parse_response(self, payload: Mapping[str, Any]) -> CompletionResult:
        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise LLMResponseError("Ollama response did not include a message object.")
        return CompletionResult(
            provider="ollama",
            model=self.config.model,
            content=content_text(message.get("content")),
            finish_reason=string_or_none(payload.get("done_reason")),
            raw=dict(payload),
        )


def normalize_provider_name(provider: str) -> ProviderName:
    normalized = provider.strip().lower()
    aliases = {
        "anthropic": "claude",
        "gemini": "google",
        "google-genai": "google",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in DEFAULT_PROVIDER_MODELS:
        supported = ", ".join(sorted(DEFAULT_PROVIDER_MODELS))
        raise LLMConfigurationError(f"Unsupported provider '{provider}'. Expected one of: {supported}.")
    return normalized  # type: ignore[return-value]


def load_provider_config(
    provider: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 60.0,
    default_temperature: float = 0.7,
    max_output_tokens: int = 1024,
) -> ProviderConfig:
    normalized = normalize_provider_name(provider)
    resolved_model = model or provider_model_from_env(normalized) or DEFAULT_PROVIDER_MODELS[normalized]
    resolved_base_url = (base_url or provider_base_url_from_env(normalized) or DEFAULT_PROVIDER_BASE_URLS[normalized]).rstrip(
        "/"
    )
    resolved_api_key = api_key or provider_api_key_from_env(normalized)

    if normalized != "ollama" and not resolved_api_key:
        env_names = ", ".join(provider_api_key_env_names(normalized))
        raise LLMConfigurationError(f"{normalized} requires an API key. Set one of: {env_names}.")

    return ProviderConfig(
        provider=normalized,
        model=resolved_model,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        timeout_seconds=timeout_seconds,
        default_temperature=default_temperature,
        max_output_tokens=max_output_tokens,
    )


def build_provider_client(
    provider: str | ProviderConfig,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 60.0,
    default_temperature: float = 0.7,
    max_output_tokens: int = 1024,
    transport: JsonTransport | None = None,
) -> BaseChatClient:
    config = provider if isinstance(provider, ProviderConfig) else load_provider_config(
        provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        default_temperature=default_temperature,
        max_output_tokens=max_output_tokens,
    )
    if config.provider == "openai":
        return OpenAIChatClient(config, transport=transport)
    if config.provider == "claude":
        return ClaudeChatClient(config, transport=transport)
    if config.provider == "google":
        return GoogleChatClient(config, transport=transport)
    return OllamaChatClient(config, transport=transport)


def openai_client(**kwargs: Any) -> BaseChatClient:
    return build_provider_client("openai", **kwargs)


def claude_client(**kwargs: Any) -> BaseChatClient:
    return build_provider_client("claude", **kwargs)


def google_client(**kwargs: Any) -> BaseChatClient:
    return build_provider_client("google", **kwargs)


def ollama_client(**kwargs: Any) -> BaseChatClient:
    return build_provider_client("ollama", **kwargs)


def rewrite_sentence(
    client: SupportsCompletion,
    *,
    sentence: str,
    focus_terms: Sequence[str] = (),
    instruction: str | None = None,
) -> str:
    payload = {
        "sentence": sentence,
        "focus_terms": list(focus_terms),
        "instruction": instruction or "Rewrite the sentence to sound natural and study-friendly.",
    }
    result = client.complete(
        [
            ChatMessage(role="system", content=SENTENCE_REWRITE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]
    )
    return normalize_text_result(result.content)


def build_dictionary_entry(
    client: SupportsCompletion,
    *,
    candidate: Mapping[str, Any],
    example_sentences: Sequence[str] = (),
    instruction: str | None = None,
) -> dict[str, Any]:
    payload = {
        "candidate": dict(candidate),
        "example_sentences": list(example_sentences),
        "instruction": instruction or "Write a compact learner-facing dictionary entry.",
    }
    result = client.complete(
        [
            ChatMessage(role="system", content=DICTIONARY_ENTRY_SYSTEM_PROMPT),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ],
        json_mode=True,
    )
    parsed = parse_json_object(result.content)
    return {
        "canonical_key": str(parsed.get("canonical_key") or candidate.get("canonical_key") or ""),
        "display": str(parsed.get("display") or candidate.get("display") or candidate.get("canonical_key") or ""),
        "part_of_speech": str(parsed.get("part_of_speech") or candidate.get("pos_group") or "unknown"),
        "english_glosses": string_list(parsed.get("english_glosses")),
        "learner_note": clean_example_text(str(parsed.get("learner_note") or "")),
        "rewritten_examples": [clean_example_text(value) for value in string_list(parsed.get("rewritten_examples"))],
        "confidence": normalized_confidence(parsed.get("confidence")),
        "source_candidate": dict(candidate),
    }


def provider_api_key_env_names(provider: ProviderName) -> tuple[str, ...]:
    if provider == "openai":
        return ("OPENAI_API_KEY",)
    if provider == "claude":
        return ("ANTHROPIC_API_KEY",)
    if provider == "google":
        return ("GOOGLE_API_KEY", "GEMINI_API_KEY")
    return ("OLLAMA_API_KEY",)


def provider_api_key_from_env(provider: ProviderName) -> str | None:
    for env_name in provider_api_key_env_names(provider):
        value = os.getenv(env_name)
        if value:
            return value
    return None


def provider_model_from_env(provider: ProviderName) -> str | None:
    env_names = {
        "openai": ("WORDBYWORD_OPENAI_MODEL", "OPENAI_MODEL"),
        "claude": ("WORDBYWORD_CLAUDE_MODEL", "ANTHROPIC_MODEL"),
        "google": ("WORDBYWORD_GOOGLE_MODEL", "GOOGLE_MODEL", "GEMINI_MODEL"),
        "ollama": ("WORDBYWORD_OLLAMA_MODEL", "OLLAMA_MODEL"),
    }[provider]
    for env_name in env_names:
        value = os.getenv(env_name)
        if value:
            return value
    return None


def provider_base_url_from_env(provider: ProviderName) -> str | None:
    env_names = {
        "openai": ("WORDBYWORD_OPENAI_BASE_URL",),
        "claude": ("WORDBYWORD_CLAUDE_BASE_URL",),
        "google": ("WORDBYWORD_GOOGLE_BASE_URL",),
        "ollama": ("WORDBYWORD_OLLAMA_BASE_URL", "OLLAMA_BASE_URL"),
    }[provider]
    for env_name in env_names:
        value = os.getenv(env_name)
        if value:
            return value
    return None


def split_system_messages(messages: Sequence[ChatMessage]) -> tuple[str, list[ChatMessage]]:
    system_parts: list[str] = []
    conversation: list[ChatMessage] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
        else:
            conversation.append(message)
    return "\n\n".join(part.strip() for part in system_parts if part.strip()), conversation


def message_row(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    return [
        {
            "role": message.role,
            "content": [{"type": "text", "text": message.content}],
        }
        for message in messages
        if message.role in {"user", "assistant"}
    ]


def google_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        role = "model" if message.role == "assistant" else "user"
        rows.append({"role": role, "parts": [{"text": message.content}]})
    return rows


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = strip_markdown_fence(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise LLMResponseError("Expected a JSON object in the model response.")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMResponseError("Model response did not contain valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError("Model response JSON was not an object.")
    return parsed


def normalize_text_result(text: str) -> str:
    cleaned = strip_markdown_fence(text).strip()
    while len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return clean_example_text(cleaned)


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = clean_example_text(value)
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = clean_example_text(item)
        if cleaned:
            items.append(cleaned)
    return items


def normalized_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return round(max(0.0, min(float(value), 1.0)), 3)
    return 0.0


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def read_error_body(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8").strip()
    except Exception:
        body = ""
    return body or exc.reason
