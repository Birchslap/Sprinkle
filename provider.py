"""
Module: provider.py
Dependencies: config.py, types.py

Handles all communication with the xAI API including streaming
and tool call processing.
"""

import json
from openai import AsyncOpenAI

from config import ModelConfig
from types import ContentDelta, ToolCallRequest, UsageData


# ============================================================
# Client
# ============================================================

def create_client(config: ModelConfig) -> AsyncOpenAI:
    """Create an async OpenAI client configured for xAI."""
    return AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
    )


# ============================================================
# Streaming
# ============================================================

async def stream_response(
    client: AsyncOpenAI,
    messages: list[dict],
    tools: list[dict] | None,
    config: ModelConfig,
):
    """
    Stream a chat completion, yielding events as they arrive.

    Yields ContentDelta for text chunks as they stream in.
    Yields ToolCallRequest for each complete tool call after the
    stream ends — tool call arguments arrive incrementally and
    must be accumulated before they can be parsed.

    The game loop consumes this generator, handles each event
    type, and orchestrates any tool call round-trips.
    """
    stream = await client.chat.completions.create(
        model=config.model,
        messages=messages,
        tools=tools if tools else None,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        stream=True,
        stream_options={"include_usage": True},
    )

    # Tool call deltas arrive in pieces — accumulate by index
    accumulated_tool_calls = {}
    usage = None

    async for chunk in stream:
        # Final chunk carries usage data but no choices
        if hasattr(chunk, "usage") and chunk.usage:
            usage = chunk.usage

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # Stream content to the player immediately
        if delta.content:
            yield ContentDelta(text=delta.content)

        # Accumulate tool call fragments
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": "",
                        "name": "",
                        "arguments": "",
                    }
                if tc_delta.id:
                    accumulated_tool_calls[idx]["id"] = tc_delta.id
                if tc_delta.function and tc_delta.function.name:
                    accumulated_tool_calls[idx]["name"] = tc_delta.function.name
                if tc_delta.function and tc_delta.function.arguments:
                    accumulated_tool_calls[idx]["arguments"] += tc_delta.function.arguments

    # Yield completed tool calls after stream is exhausted
    for idx in sorted(accumulated_tool_calls.keys()):
        tc = accumulated_tool_calls[idx]
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {"_raw": tc["arguments"]}
        yield ToolCallRequest(
            id=tc["id"],
            name=tc["name"],
            arguments=args,
        )

    # Yield usage data last
    if usage:
        yield UsageData(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cached_tokens=getattr(usage, "prompt_tokens_details", {}).get("cached_tokens", 0) if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details else 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )


# ============================================================
# Message Builders
# ============================================================

def build_tool_result_message(tool_call_id: str, result: str) -> dict:
    """Build a tool result message for the API.

    After dispatching a tool call, the result must be sent back
    to the model in this format for the conversation to continue.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": result,
    }


def build_assistant_tool_call_message(tool_calls: list[ToolCallRequest]) -> dict:
    """Build the assistant message that contains tool call requests.

    The API requires the assistant's tool call message to appear
    in the conversation history before the corresponding tool
    result messages.
    """
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in tool_calls
        ],
    }
