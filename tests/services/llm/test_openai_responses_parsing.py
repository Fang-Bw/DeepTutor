"""Regression tests for OpenAI Responses API stream parsing."""

from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace

import httpx
import pytest

from deeptutor.services.llm.exceptions import LLMProviderTransportError
from deeptutor.services.llm.provider_core.openai_responses.parsing import (
    consume_sdk_stream,
    consume_sse,
    parse_response_output,
)


class _SSEFixture:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def aiter_lines(self):
        for event in self._events:
            yield f"data: {json.dumps(event)}"
            yield ""


async def _sdk_events(events):
    for event in events:
        yield event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "partial_response"),
    [
        ([], False),
        ([{"type": "response.created", "response": {"id": "resp_1"}}], False),
        ([{"type": "response.output_text.delta", "delta": "unfinished"}], True),
        (
            [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "{}",
                    },
                }
            ],
            True,
        ),
        ([{"type": "response.reasoning_summary_text.delta", "delta": "thinking"}], True),
    ],
)
async def test_sse_eof_without_response_terminal_event_fails(
    events: list[dict], partial_response: bool
) -> None:
    visible: list[str] = []

    async def on_content_delta(delta: str) -> None:
        visible.append(delta)

    with pytest.raises(LLMProviderTransportError, match="before a terminal") as caught:
        await consume_sse(_SSEFixture(events), on_content_delta)

    assert caught.value.partial_response is partial_response
    assert visible == [
        event["delta"] for event in events if event["type"] == "response.output_text.delta"
    ]


@pytest.mark.asyncio
async def test_sse_done_marker_does_not_replace_response_completed() -> None:
    response = httpx.Response(200, content=b"data: [DONE]\n\n")

    with pytest.raises(LLMProviderTransportError, match="before a terminal") as caught:
        await consume_sse(response)

    assert caught.value.partial_response is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_event", ['{"type":', "[]"])
async def test_sse_malformed_event_does_not_silently_complete(bad_event: str) -> None:
    response = httpx.Response(
        200,
        content=(
            'data: {"type":"response.output_text.delta","delta":"part"}\n\n'
            f"data: {bad_event}\n\n"
            'data: {"type":"response.completed"}\n\n'
        ).encode(),
    )

    with pytest.raises(LLMProviderTransportError, match="invalid event") as caught:
        await consume_sse(response)

    assert caught.value.partial_response is True


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_type", ["response.completed", "response.incomplete"])
async def test_sse_terminal_event_does_not_wait_for_connection_to_close(
    terminal_type: str,
) -> None:
    class Response:
        async def aiter_lines(self):
            yield 'data: {"type":"response.output_text.delta","delta":"answer"}'
            yield ""
            yield f'data: {{"type":"{terminal_type}"}}'
            yield ""
            raise AssertionError("A terminal response must end consumption")

    content, _, finish_reason = await consume_sse(Response())

    assert content == "answer"
    assert finish_reason == ("length" if terminal_type == "response.incomplete" else "stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_response", [False, True])
async def test_sse_transport_failure_preserves_partial_output(partial_response: bool) -> None:
    class Response:
        async def aiter_lines(self):
            if partial_response:
                yield 'data: {"type":"response.output_text.delta","delta":"answer"}'
                yield ""
            raise httpx.ReadError("private-proxy-secret")

    with pytest.raises(LLMProviderTransportError, match="transport failed") as caught:
        await consume_sse(Response())

    assert caught.value.partial_response is partial_response
    assert "private-proxy-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_sse_arguments_can_be_correlated_by_item_id() -> None:
    response = _SSEFixture(
        [
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "lookup",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"topic":',
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_1",
                "arguments": '{"topic":"algebra"}',
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "lookup",
                },
            },
        ]
    )

    response._events.append({"type": "response.completed"})
    _, tool_calls, _ = await consume_sse(response)  # type: ignore[arg-type]

    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_1|fc_1"
    assert tool_calls[0].arguments == {"topic": "algebra"}


@pytest.mark.asyncio
async def test_sdk_arguments_can_be_correlated_by_item_id() -> None:
    function_call = SimpleNamespace(
        type="function_call",
        id="fc_1",
        call_id="call_1",
        name="lookup",
        arguments="",
    )
    events = [
        SimpleNamespace(type="response.output_item.added", item=function_call),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc_1",
            delta='{"topic":',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="fc_1",
            arguments='{"topic":"geometry"}',
        ),
        SimpleNamespace(type="response.output_item.done", item=function_call),
    ]

    _, tool_calls, _, _, _ = await consume_sdk_stream(_sdk_events(events))

    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_1|fc_1"
    assert tool_calls[0].arguments == {"topic": "geometry"}


@pytest.mark.asyncio
async def test_sdk_preserves_deepseek_reasoning_text_for_next_tool_round() -> None:
    reasoning_item = SimpleNamespace(
        type="reasoning",
        id="rs_1",
        status="completed",
        content=[{"type": "reasoning_text", "text": "Need to inspect the MCP status."}],
        summary=[],
    )
    function_call = SimpleNamespace(
        type="function_call",
        id="fc_1",
        call_id="call_1",
        name="check_mcp",
        arguments="{}",
    )
    events = [
        SimpleNamespace(type="response.reasoning_text.delta", delta="Need to inspect "),
        SimpleNamespace(type="response.reasoning_text.delta", delta="the MCP status."),
        SimpleNamespace(type="response.output_item.done", item=reasoning_item),
        SimpleNamespace(type="response.output_item.added", item=function_call),
        SimpleNamespace(type="response.output_item.done", item=function_call),
    ]
    provider_events: list[tuple[str, dict]] = []

    _, tool_calls, _, _, reasoning = await consume_sdk_stream(
        _sdk_events(events),
        on_provider_event=lambda kind, payload: provider_events.append((kind, payload)),
    )

    assert reasoning == "Need to inspect the MCP status."
    assert tool_calls[0].name == "check_mcp"
    assert provider_events == [
        ("output_item", vars(reasoning_item)),
        ("output_item", vars(function_call)),
    ]


@pytest.mark.asyncio
async def test_sdk_incomplete_maps_to_length_and_keeps_reasoning_usage() -> None:
    events = [
        SimpleNamespace(type="response.reasoning_text.delta", delta="thinking only"),
        SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=8000,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=8000),
                ),
            ),
        ),
    ]

    content, tool_calls, finish_reason, usage, reasoning = await consume_sdk_stream(
        _sdk_events(events)
    )

    assert content == ""
    assert tool_calls == []
    assert finish_reason == "length"
    assert usage == {
        "prompt_tokens": 100,
        "completion_tokens": 8000,
        "total_tokens": 8100,
        "reasoning_tokens": 8000,
    }
    assert reasoning == "thinking only"


@pytest.mark.asyncio
async def test_sse_incomplete_maps_to_length_and_reports_usage() -> None:
    provider_events: list[tuple[str, dict]] = []
    response = _SSEFixture(
        [
            {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 9,
                        "output_tokens_details": {"reasoning_tokens": 9},
                    },
                },
            }
        ]
    )

    _content, _tool_calls, finish_reason = await consume_sse(
        response,
        on_provider_event=lambda kind, payload: provider_events.append((kind, payload)),
    )

    assert finish_reason == "length"
    assert provider_events == [
        (
            "usage",
            {
                "prompt_tokens": 4,
                "completion_tokens": 9,
                "total_tokens": 13,
                "reasoning_tokens": 9,
            },
        )
    ]


def test_incomplete_content_filter_is_not_treated_as_token_truncation() -> None:
    result = parse_response_output(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "content_filter"},
            "output": [],
        }
    )

    assert result.finish_reason == "content_filter"


def test_nonstream_incomplete_maps_usage_and_reasoning_tokens() -> None:
    result = parse_response_output(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [{"type": "summary_text", "text": "thinking"}],
                }
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 8000,
                "output_tokens_details": {"reasoning_tokens": 8000},
            },
        }
    )

    assert result.finish_reason == "length"
    assert result.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 8000,
        "total_tokens": 8100,
        "reasoning_tokens": 8000,
    }
    assert result.reasoning_content == "thinking"


def test_nonstream_response_preserves_deepseek_reasoning_text_and_native_items() -> None:
    reasoning_item = {
        "type": "reasoning",
        "id": "rs_1",
        "status": "completed",
        "content": [{"type": "reasoning_text", "text": "Need to inspect the MCP status."}],
        "summary": [],
    }
    message_item = {
        "type": "message",
        "id": "msg_1",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "MCP is healthy."}],
    }

    result = parse_response_output(
        {
            "status": "completed",
            "output": [reasoning_item, message_item],
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }
    )

    assert result.content == "MCP is healthy."
    assert result.reasoning_content == "Need to inspect the MCP status."
    assert result.provider_specific_fields["native_output_items"] == [
        reasoning_item,
        message_item,
    ]


@pytest.mark.asyncio
async def test_sdk_failed_terminal_event_is_not_misreported_as_stop() -> None:
    with pytest.raises(RuntimeError, match="Response failed"):
        await consume_sdk_stream(
            _sdk_events(
                [
                    SimpleNamespace(
                        type="response.failed",
                        response=SimpleNamespace(
                            error=SimpleNamespace(message="provider rejected the request")
                        ),
                    )
                ]
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["sse", "sdk"])
async def test_argument_deltas_are_preserved_without_a_done_event(consumer: str) -> None:
    """Cover delta accumulation independently from the final replacement event."""
    item = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "lookup",
    }
    events = [
        {"type": "response.output_item.added", "item": item},
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"topic":',
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '"calculus"}',
        },
        {"type": "response.output_item.done", "item": item},
    ]

    if consumer == "sse":
        events.append({"type": "response.completed"})
        _, tool_calls, _ = await consume_sse(_SSEFixture(events))
    else:
        sdk_events = [
            SimpleNamespace(
                **{
                    **event,
                    "item": SimpleNamespace(**event["item"]),
                }
            )
            if "item" in event
            else SimpleNamespace(**event)
            for event in events
        ]
        _, tool_calls, _, _, _ = await consume_sdk_stream(_sdk_events(sdk_events))

    assert tool_calls[0].arguments == {"topic": "calculus"}


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["sse", "sdk"])
async def test_response_failed_raises_the_provider_error(consumer: str) -> None:
    error = {"code": "server_error", "message": "The model failed to generate a response."}

    with pytest.raises(RuntimeError, match="server_error: The model failed"):
        if consumer == "sse":
            await consume_sse(
                _SSEFixture([{"type": "response.failed", "response": {"error": error}}])
            )
        else:
            event = SimpleNamespace(
                type="response.failed",
                response=SimpleNamespace(error=SimpleNamespace(**error)),
            )
            await consume_sdk_stream(_sdk_events([event]))


@pytest.mark.asyncio
async def test_sdk_top_level_error_event_raises() -> None:
    event = SimpleNamespace(type="error", code="rate_limit_exceeded", message="Try again later")

    with pytest.raises(RuntimeError, match="Try again later"):
        await consume_sdk_stream(_sdk_events([event]))


@pytest.mark.asyncio
async def test_a_call_without_an_item_id_does_not_inherit_another_calls_identity() -> None:
    """The placeholder item id is not an identity, and must never resolve one.

    A provider that omits ``item.id`` on function-call items makes every call
    carry the same stand-in. If that stand-in were registered as a lookup key,
    a ``done`` event for a call that was never announced would find the
    previous call's buffer — and the tool would be dispatched under the wrong
    name with the wrong arguments.
    """
    events = [
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "call_id": "call_1", "name": "delete_kb"},
        },
        {
            "type": "response.function_call_arguments.done",
            "call_id": "call_1",
            "arguments": '{"kb":"secret"}',
        },
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_1", "name": "delete_kb"},
        },
        # Never announced with an ``added`` event, and carries no item id.
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call_2",
                "name": "list_kb",
                "arguments": '{"scope":"mine"}',
            },
        },
    ]

    events.append({"type": "response.completed"})
    _, tool_calls, _ = await consume_sse(_SSEFixture(events))

    assert [(call.name, call.arguments) for call in tool_calls] == [
        ("delete_kb", {"kb": "secret"}),
        ("list_kb", {"scope": "mine"}),
    ]


@pytest.mark.asyncio
async def test_sse_reports_arguments_as_they_stream() -> None:
    """The hook sees the accumulated text, not the individual fragments."""
    response = _SSEFixture(
        [
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "ask_user",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"intro":',
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": ' "Which?"}',
            },
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "ask_user",
                },
            },
        ]
    )
    seen: list[tuple[str, str, str]] = []

    async def _on_tool_args_delta(call_id: str, name: str, arguments: str) -> None:
        seen.append((call_id, name, arguments))

    response._events.append({"type": "response.completed"})
    _, tool_calls, _ = await consume_sse(
        response,
        on_tool_args_delta=_on_tool_args_delta,
    )

    assert seen == [
        ("call_1", "ask_user", '{"intro":'),
        ("call_1", "ask_user", '{"intro": "Which?"}'),
    ]
    # The side channel does not disturb the dispatched call.
    assert len(tool_calls) == 1
    assert tool_calls[0].arguments == {"intro": "Which?"}


@pytest.mark.asyncio
async def test_sdk_stream_reports_arguments_as_they_stream() -> None:
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="ask_user",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc_1",
            call_id=None,
            delta='{"intro": "W',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="fc_1",
            call_id=None,
            arguments='{"intro": "Which?"}',
        ),
    ]
    seen: list[str] = []

    async def _on_tool_args_delta(call_id: str, name: str, arguments: str) -> None:
        seen.append(arguments)

    await consume_sdk_stream(
        _sdk_events(events),
        on_tool_args_delta=_on_tool_args_delta,
    )

    assert seen == ['{"intro": "W']


@pytest.mark.asyncio
async def test_unnamed_call_is_not_previewed() -> None:
    """A delta that cannot be attributed to a named tool is dropped."""
    response = _SSEFixture(
        [
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_unknown",
                "delta": "{",
            },
        ]
    )
    seen: list[str] = []

    async def _on_tool_args_delta(call_id: str, name: str, arguments: str) -> None:
        seen.append(arguments)

    response._events.append({"type": "response.completed"})
    await consume_sse(response, on_tool_args_delta=_on_tool_args_delta)

    assert seen == []


def _function_call_response(arguments: str) -> dict:
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "ask_user",
                "arguments": arguments,
            }
        ],
    }


def test_arguments_with_an_unescaped_quote_are_recovered_intact() -> None:
    """A model describing an option as ``路径名"1"`` still produces the card.

    Strict JSON rejects the inner quote; repair recovers the field verbatim.
    """
    arguments = (
        '{"questions": [{"id": "q1", "prompt": "Which?", "options": '
        '[{"label": "A", "description": "路径名"1"，15 goals"}]}]}'
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(arguments)

    parsed = parse_response_output(_function_call_response(arguments))

    option = parsed.tool_calls[0].arguments["questions"][0]["options"][0]
    assert option["description"] == '路径名"1"，15 goals'


@contextmanager
def _captured_warnings():
    """Collect this module's loguru warnings for the duration of the block."""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(message.record["message"]),
        level="WARNING",
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def test_recovering_repairable_arguments_is_not_logged_as_a_failure() -> None:
    """The warning read as a broken card; the card was in fact complete."""
    arguments = '{"intro": "路径名"1""}'
    with _captured_warnings() as warnings:
        parse_response_output(_function_call_response(arguments))

    assert warnings == []


def test_arguments_repair_cannot_salvage_are_reported() -> None:
    with _captured_warnings() as warnings:
        parsed = parse_response_output(_function_call_response("@@@ not json @@@"))

    assert parsed.tool_calls[0].arguments == {"raw": "@@@ not json @@@"}
    assert any("Could not parse tool call arguments" in message for message in warnings)
