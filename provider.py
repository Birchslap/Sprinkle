"""
Module: provider.py
Dependencies: config.py

Handles all communication with the xAI API including streaming
and tool call processing.
"""

import json
from dataclasses import dataclass
from openai import AsyncOpenAI

from config import ModelConfig


# ============================================================
# Event Types
# ============================================================

@dataclass
class ContentDelta:
    """A chunk of streamed content text."""
    text: str


@dataclass
class ToolCallRequest:
    """A complete tool call request from the model."""
    id: str
    name: str
    arguments: dict


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

async def stream_response(client, messages, tools, config):
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
    )

    # Tool call deltas arrive in pieces — accumulate by index
    tool_calls_acc = {}

    async for chunk in stream:
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
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": "",
                        "name": "",
                        "arguments": "",
                    }
                if tc_delta.id:
                    tool_calls_acc[idx]["id"] = tc_delta.id
                if tc_delta.function and tc_delta.function.name:
                    tool_calls_acc[idx]["name"] = tc_delta.function.name
                if tc_delta.function and tc_delta.function.arguments:
                    tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

    # Yield completed tool calls after stream is exhausted
    for idx in sorted(tool_calls_acc.keys()):
        tc = tool_calls_acc[idx]
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {"_raw": tc["arguments"]}
        yield ToolCallRequest(
            id=tc["id"],
            name=tc["name"],
            arguments=args,
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
