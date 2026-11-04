from __future__ import annotations

from contextlib import asynccontextmanager
import json
from typing import Any, AsyncIterator

import httpcore
import httpx
import pytest

from deeptutor.runtime.agentic.client import _ProviderOpenAIAdapter
from deeptutor.runtime.agentic.usage import UsageTracker, record_streamed_usage
from deeptutor.services.codex_auth.constants import CODEX_RESPONSES_URL
from deeptutor.services.codex_auth.contracts import CodexAuthError, CodexToken
from deeptutor.services.llm.exceptions import LLMProviderTransportError
from deeptutor.services.llm.provider_core import openai_codex_provider as module
from deeptutor.services.llm.provider_core.openai_codex_provider import (
    CodexHTTPError,
    OpenAICodexProvider,
)


class FakeCodexService:
    def __init__(self) -> None:
        self.token = CodexToken(
            access_token="test-access-token",
            account_id="account-123",
            expires_at=2_000_000_000,
            generation=7,
        )
        self.token_calls = 0
        self.guard_entries = 0
        self.recovered_generation: int | None = None
        self.marked_reauth: bool = False
        self.runtime_validations: list[tuple[CodexToken, str, str | None]] = []

    async def get_token(self) -> CodexToken:
        self.token_calls += 1
        return self.token

    async def recover_after_unauthorized(self, generation: int) -> None:
        self.recovered_generation = generation

    def validate_runtime_profile(
        self,
        token: CodexToken,
        model_slug: str,
        reasoning_effort: str | None,
    ) -> None:
        self.runtime_validations.append((token, model_slug, reasoning_effort))

    def mark_reauth_required(self) -> None:
        self.marked_reauth = True

    @asynccontextmanager
    async def inference_guard(self) -> AsyncIterator[None]:
        self.guard_entries += 1
        yield


def _mock_codex_stream(monkeypatch: pytest.MonkeyPatch, events: list[dict]) -> None:
    """Exercise the actual HTTP/SSE path with an in-memory transport."""
    content = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()

    async def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CODEX_RESPONSES_URL
        return httpx.Response(200, content=content)

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    monkeypatch.setattr(module, "codex_proxy_options", lambda: {})
    monkeypatch.setattr(module, "get_codex_oauth_service", FakeCodexService)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_codex_reports_usage_and_replayable_reasoning_metadata(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    reasoning = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque-state"}
    message = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hello"}],
    }
    _mock_codex_stream(
        monkeypatch,
        [
            {"type": "response.output_item.done", "item": reasoning},
            {"type": "response.output_text.delta", "delta": "hello"},
            {
                "type": "response.output_text.annotation.added",
                "annotation": {
                    "type": "url_citation",
                    "url": "https://example.com",
                    "title": "Source",
                },
            },
            {"type": "response.output_item.done", "item": message},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 8,
                        "total_tokens": 28,
                        "input_tokens_details": {"cached_tokens": 10},
                        "output_tokens_details": {"reasoning_tokens": 3},
                    },
                },
            },
        ],
    )
    provider = OpenAICodexProvider()
    call = provider.chat_stream if stream else provider.chat
    result = await call(messages=[{"role": "user", "content": "hello"}])

    assert result.content == "hello"
    assert result.finish_reason == "stop"
    assert result.usage == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
        "cache_read_input_tokens": 10,
        "reasoning_tokens": 3,
    }
    assert result.provider_specific_fields == {
        "native_output_items": [reasoning, message],
        "citations": [{"url": "https://example.com", "title": "Source"}],
    }


@pytest.mark.asyncio
async def test_codex_usage_reaches_runtime_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_codex_stream(
        monkeypatch,
        [
            {"type": "response.output_text.delta", "delta": "hello"},
            {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 20, "output_tokens": 8}},
            },
        ],
    )
    adapter = _ProviderOpenAIAdapter(OpenAICodexProvider())
    stream = await adapter.chat.completions.create(
        model="openai-codex/gpt-5.6-sol",
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    )
    chunks = [chunk async for chunk in stream]
    usage_frames = [chunk.usage for chunk in chunks if chunk.usage]
    tracker = UsageTracker()
    record_streamed_usage(tracker, usage_frames[-1])

    assert len(usage_frames) == 1
    assert tracker.prompt_tokens == 20
    assert tracker.completion_tokens == 8
    assert tracker.total_tokens == 28
    assert tracker.calls == 1


@pytest.mark.asyncio
async def test_codex_missing_usage_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_codex_stream(monkeypatch, [{"type": "response.completed"}])

    result = await OpenAICodexProvider().chat(messages=[{"role": "user", "content": "hello"}])

    assert result.usage == {}
    assert result.provider_specific_fields == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_response", [False, True])
async def test_codex_truncated_stream_preserves_retry_guard_through_adapter(
    monkeypatch: pytest.MonkeyPatch, partial_response: bool
) -> None:
    _mock_codex_stream(
        monkeypatch,
        [{"type": "response.output_text.delta", "delta": "unfinished"}] if partial_response else [],
    )
    adapter = _ProviderOpenAIAdapter(OpenAICodexProvider())
    stream = await adapter.chat.completions.create(
        model="openai-codex/gpt-5.6-sol",
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    )
    chunks = []

    with pytest.raises(LLMProviderTransportError, match="before a terminal") as caught:
        async for chunk in stream:
            chunks.append(chunk)

    assert caught.value.partial_response is partial_response
    assert [chunk.choices[0].delta.content for chunk in chunks] == (
        ["unfinished"] if partial_response else []
    )
    assert all(chunk.choices[0].finish_reason is None for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_slug", "reasoning_effort"),
    [("gpt-5.6-sol", "medium"), ("gpt-5.6-luna", "none")],
)
async def test_provider_uses_deeptutor_token_service_and_raw_model_id(
    monkeypatch: pytest.MonkeyPatch,
    model_slug: str,
    reasoning_effort: str,
) -> None:
    service = FakeCodexService()
    requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    async def request(
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[str, list[Any], str]:
        requests.append((url, headers, body))
        return "ok", [], "stop"

    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", request)

    image_url = "data:image/png;base64,QUJD"
    result = await OpenAICodexProvider().chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        model=f"openai-codex/{model_slug}",
        reasoning_effort=reasoning_effort,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look something up",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    url, headers, body = requests[0]
    assert result.finish_reason == "stop"
    assert service.token_calls == 1
    assert service.guard_entries == 1
    assert service.runtime_validations == [(service.token, model_slug, reasoning_effort)]
    assert url == CODEX_RESPONSES_URL
    assert headers["Authorization"] == "Bearer test-access-token"
    assert headers["chatgpt-account-id"] == "account-123"
    assert body["model"] == model_slug
    assert body["reasoning"] == {"effort": reasoning_effort}
    assert body["tools"][0]["name"] == "lookup"
    assert body["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "describe"},
                {"type": "input_image", "image_url": image_url, "detail": "auto"},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_401_refreshes_for_next_request_without_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeCodexService()
    request_count = 0

    async def rejected_request(*_args: Any, **_kwargs: Any) -> tuple[str, list[Any], str]:
        nonlocal request_count
        request_count += 1
        raise CodexHTTPError(401, module._friendly_error(401))

    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", rejected_request)

    result = await OpenAICodexProvider().chat(
        [{"role": "user", "content": "hello"}],
        model="gpt-5.6-sol",
    )

    assert request_count == 1
    assert service.recovered_generation == service.token.generation
    assert result.finish_reason == "error"
    assert "retry" in result.content.lower()


@pytest.mark.asyncio
async def test_401_with_dead_refresh_token_does_not_promise_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telling the user "already refreshed, retry" would loop them forever."""
    service = FakeCodexService()

    async def failed_recovery(generation: int) -> None:
        raise CodexAuthError(
            "authentication_required",
            "Sign in to Codex before using this model.",
            401,
        )

    async def rejected_request(*_args: Any, **_kwargs: Any) -> tuple[str, list[Any], str]:
        raise CodexHTTPError(401, module._friendly_error(401))

    service.recover_after_unauthorized = failed_recovery  # type: ignore[method-assign]
    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", rejected_request)

    result = await OpenAICodexProvider().chat(
        [{"role": "user", "content": "hello"}],
        model="gpt-5.6-sol",
    )

    assert result.finish_reason == "error"
    assert "retry" not in result.content.lower()
    assert "sign in again" in result.content.lower()


@pytest.mark.asyncio
async def test_403_does_not_mark_reauth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 may be model access denial, not a revoked OAuth grant."""
    service = FakeCodexService()

    async def rejected_request(*_args: Any, **_kwargs: Any) -> tuple[str, list[Any], str]:
        raise CodexHTTPError(403, module._friendly_error(403))

    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", rejected_request)

    result = await OpenAICodexProvider().chat(
        [{"role": "user", "content": "hello"}],
        model="gpt-5.6-sol",
    )

    assert result.finish_reason == "error"
    assert "account" in result.content.lower()
    assert service.marked_reauth is False


@pytest.mark.asyncio
async def test_429_never_reads_openai_api_key_or_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeCodexService()
    destinations: list[str] = []
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")

    async def rate_limited(
        url: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[str, list[Any], str]:
        destinations.append(url)
        raise CodexHTTPError(429, "Codex usage quota exceeded.")

    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", rate_limited)

    result = await OpenAICodexProvider().chat(
        [{"role": "user", "content": "hello"}],
        model="gpt-5.6-sol",
    )

    assert "Codex usage quota" in result.content
    assert destinations == [CODEX_RESPONSES_URL]
    assert service.token_calls == 1


@pytest.mark.asyncio
async def test_provider_does_not_expose_network_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeCodexService()

    async def network_failure(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[str, list[Any], str]:
        raise RuntimeError("private proxy host and token")

    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", network_failure)

    result = await OpenAICodexProvider().chat(
        [{"role": "user", "content": "hello"}],
    )

    assert result.finish_reason == "error"
    assert "private proxy" not in result.content
    assert "token" not in result.content.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.RemoteProtocolError("private proxy host and token"),
        httpcore.RemoteProtocolError("private proxy host and token"),
    ],
)
async def test_transport_failure_raises_sanitized_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
    transport_error: Exception,
) -> None:
    service = FakeCodexService()
    sensitive_message = "private proxy host and token"

    async def transport_failure(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[str, list[Any], str]:
        raise transport_error

    monkeypatch.setattr(module, "get_codex_oauth_service", lambda: service)
    monkeypatch.setattr(module, "_request_codex", transport_failure)

    with pytest.raises(LLMProviderTransportError) as exc_info:
        await OpenAICodexProvider().chat(
            [{"role": "user", "content": "hello"}],
        )

    assert str(exc_info.value) == "Codex transport request failed."
    assert sensitive_message not in str(exc_info.value)
    assert exc_info.value.__cause__ is transport_error


def test_provider_source_no_longer_imports_oauth_cli_kit() -> None:
    source = module.__loader__.get_source(module.__name__)  # type: ignore[union-attr]

    assert source is not None
    assert "oauth_cli_kit" not in source
    assert "get_codex_oauth_service" in source
    assert OpenAICodexProvider().get_default_model() == "openai-codex/gpt-5.6-sol"
