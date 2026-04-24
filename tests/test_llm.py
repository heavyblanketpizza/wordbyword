from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from wordbyword.llm import (
    ChatMessage,
    CompletionResult,
    build_dictionary_entry,
    build_provider_client,
    load_provider_config,
    rewrite_sentence,
)


class ProviderConfigTest(unittest.TestCase):
    def test_openai_config_reads_env_and_trims_base_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "openai-key",
                "WORDBYWORD_OPENAI_MODEL": "gpt-test",
                "WORDBYWORD_OPENAI_BASE_URL": "https://example.com/v1/",
            },
            clear=True,
        ):
            config = load_provider_config("openai")

        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model, "gpt-test")
        self.assertEqual(config.api_key, "openai-key")
        self.assertEqual(config.base_url, "https://example.com/v1")

    def test_google_config_accepts_gemini_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "gemini-key",
                "GEMINI_MODEL": "gemini-test",
            },
            clear=True,
        ):
            config = load_provider_config("google")

        self.assertEqual(config.provider, "google")
        self.assertEqual(config.api_key, "gemini-key")
        self.assertEqual(config.model, "gemini-test")

    def test_ollama_config_does_not_require_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_provider_config("ollama")

        self.assertEqual(config.provider, "ollama")
        self.assertEqual(config.base_url, "http://localhost:11434")
        self.assertIsNone(config.api_key)


class ProviderClientTest(unittest.TestCase):
    def test_openai_client_builds_chat_completion_request(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {"content": "rewritten sentence"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

        client = build_provider_client(
            "openai",
            model="gpt-test",
            api_key="openai-key",
            transport=transport,
        )
        result = client.complete(
            [ChatMessage("system", "sys"), ChatMessage("user", "hello")],
            json_mode=True,
        )

        self.assertEqual(result.content, "rewritten sentence")
        self.assertEqual(transport.url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(transport.headers["Authorization"], "Bearer openai-key")
        self.assertEqual(transport.body["model"], "gpt-test")
        self.assertEqual(transport.body["response_format"], {"type": "json_object"})
        self.assertEqual(transport.body["messages"][0]["role"], "system")

    def test_claude_client_sends_system_prompt_separately(self) -> None:
        transport = FakeTransport(
            {
                "content": [{"type": "text", "text": "dictionary entry"}],
                "stop_reason": "end_turn",
            }
        )

        client = build_provider_client(
            "claude",
            model="claude-test",
            api_key="anthropic-key",
            transport=transport,
        )
        result = client.complete([ChatMessage("system", "sys"), ChatMessage("user", "hello")])

        self.assertEqual(result.content, "dictionary entry")
        self.assertEqual(transport.url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(transport.headers["x-api-key"], "anthropic-key")
        self.assertEqual(transport.body["system"], "sys")
        self.assertEqual(transport.body["messages"][0]["content"][0]["text"], "hello")

    def test_google_client_uses_generate_content_endpoint(self) -> None:
        transport = FakeTransport(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "google output"}]},
                        "finishReason": "STOP",
                    }
                ]
            }
        )

        client = build_provider_client(
            "google",
            model="gemini-test",
            api_key="google-key",
            transport=transport,
        )
        result = client.complete([ChatMessage("system", "sys"), ChatMessage("user", "hello")], json_mode=True)

        self.assertEqual(result.content, "google output")
        self.assertIn("/models/gemini-test:generateContent?key=google-key", transport.url)
        self.assertEqual(transport.body["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(transport.body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(transport.body["contents"][0]["role"], "user")

    def test_ollama_client_uses_native_chat_api(self) -> None:
        transport = FakeTransport(
            {
                "message": {"content": "ollama output"},
                "done_reason": "stop",
            }
        )

        client = build_provider_client(
            "ollama",
            model="llama-test",
            base_url="http://localhost:11434/",
            transport=transport,
        )
        result = client.complete([ChatMessage("user", "hello")], json_mode=True)

        self.assertEqual(result.content, "ollama output")
        self.assertEqual(transport.url, "http://localhost:11434/api/chat")
        self.assertEqual(transport.body["format"], "json")
        self.assertEqual(transport.body["options"]["num_predict"], 1024)
        self.assertEqual(transport.body["messages"][0]["content"], "hello")


class TaskHelperTest(unittest.TestCase):
    def test_rewrite_sentence_normalizes_wrapped_output(self) -> None:
        client = StubCompletionClient('"  "학생이 책을 읽는다."  "')

        rewritten = rewrite_sentence(client, sentence="학생은 책을 읽고 있다.")

        self.assertEqual(rewritten, "학생이 책을 읽는다.")

    def test_build_dictionary_entry_parses_json_payload(self) -> None:
        client = StubCompletionClient(
            """```json
            {
              "canonical_key": "읽다",
              "display": "읽다",
              "part_of_speech": "verb",
              "english_glosses": ["to read"],
              "learner_note": "문자를 소리 내거나 뜻으로 이해할 때 쓴다.",
              "rewritten_examples": ["학생이 책을 읽는다."],
              "confidence": 0.82
            }
            ```"""
        )

        entry = build_dictionary_entry(
            client,
            candidate={"canonical_key": "읽다", "display": "읽다", "pos_group": "verb"},
            example_sentences=["학생은 책을 읽고 있다."],
        )

        self.assertEqual(entry["canonical_key"], "읽다")
        self.assertEqual(entry["part_of_speech"], "verb")
        self.assertEqual(entry["english_glosses"], ["to read"])
        self.assertEqual(entry["rewritten_examples"], ["학생이 책을 읽는다."])
        self.assertEqual(entry["confidence"], 0.82)
        self.assertEqual(entry["source_candidate"]["canonical_key"], "읽다")


class FakeTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.url = ""
        self.headers: dict[str, str] = {}
        self.body: dict[str, object] = {}

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        self.url = url
        self.headers = headers
        self.body = body
        return self.response


class StubCompletionClient:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        return CompletionResult(provider="openai", model="gpt-test", content=self.content)
