"""
Module: game.py
Dependencies: config.py, db.py, provider.py, tools.py, types.py

Core game loop. Manages conversation state, streams model responses,
dispatches tool calls, and persists every message to the transcript.
"""

import json
import logging
from typing import AsyncGenerator

import asyncpg

log = logging.getLogger(__name__)

from config import AppConfig
from db import (
    create_campaign, create_session, end_session,
    save_message, get_messages, get_last_turn_id,
)
from provider import (
    create_client, stream_response,
    build_assistant_tool_call_message, build_tool_result_message,
)
from tools import TOOL_DEFINITIONS, dispatch_tool
from types import ContentDelta, ToolCallRequest


# -- Constants ----------------------------------------------------------------

MAX_TOOL_ROUNDS = 15
HISTORY_LIMIT = 50


# -- Game State ---------------------------------------------------------------

class GameState:
    """Holds the mutable state for a running game session."""

    def __init__(self, pool: asyncpg.Pool, config: AppConfig):
        self.pool = pool
        self.config = config
        self.client = create_client(config.model)
        self.campaign_id: int | None = None
        self.session_id: int | None = None
        self.turn_id: int = 0
        self.system_prompt: str = ""

    async def start_campaign(self, name: str, system_prompt: str,
                             setting: str | None = None) -> dict:
        """Create a new campaign and its first session."""
        campaign = await create_campaign(self.pool, name, setting)
        self.campaign_id = campaign["id"]
        self.system_prompt = system_prompt
        session = await create_session(self.pool, self.campaign_id)
        self.session_id = session["id"]
        self.turn_id = 0
        return campaign

    async def resume_campaign(self, campaign_id: int, system_prompt: str) -> dict:
        """Resume an existing campaign with a new session."""
        self.campaign_id = campaign_id
        self.system_prompt = system_prompt
        session = await create_session(self.pool, self.campaign_id)
        self.session_id = session["id"]
        self.turn_id = await get_last_turn_id(self.pool, self.campaign_id)
        return {"campaign_id": campaign_id, "session_id": session["id"]}

    async def end(self, summary: str | None = None) -> None:
        """End the current session."""
        if self.session_id:
            await end_session(self.pool, self.session_id, summary)


# -- Message Building ---------------------------------------------------------

async def _build_messages(state: GameState) -> list[dict[str, str]]:
    """Build the message list for the API call.

    Structure: system prompt, then recent history in chronological order.
    History is pulled from the database so it survives across reconnections.
    """
    messages = [{"role": "system", "content": state.system_prompt}]

    rows = await get_messages(state.pool, state.session_id, HISTORY_LIMIT)
    rows.reverse()  # get_messages returns newest-first

    for row in rows:
        if row.get("tool_name"):
            # Tool result message
            tool_data = row.get("tool_data") or {}
            messages.append({
                "role": "tool",
                "tool_call_id": tool_data.get("tool_call_id", ""),
                "content": row["content"],
            })
        elif row["role"] == "assistant" and row.get("tool_data"):
            # Assistant message containing tool calls
            messages.append(row["tool_data"])
        else:
            messages.append({
                "role": row["role"],
                "content": row["content"],
            })

    return messages


# -- Tool Dispatch ------------------------------------------------------------

async def _handle_tool_rounds(
    state: GameState,
    turn_id: int,
    tool_calls: list[ToolCallRequest],
    content: str,
) -> None:
    """Dispatch tool calls, persist everything, and log failures.

    Saves the assistant's tool-call message, then dispatches each call
    and saves the result. Errors are logged with full tracebacks but
    don't crash the turn — the model receives the error as a tool result
    and can recover.
    """
    # Save the assistant message that contained the tool calls
    assistant_msg = build_assistant_tool_call_message(tool_calls)
    await save_message(
        state.pool, state.session_id, turn_id,
        role="assistant", content=content or "",
        tool_data=assistant_msg,
    )

    # Dispatch each tool call and save results
    for tc in tool_calls:
        try:
            result = await dispatch_tool(
                tc.name, state.pool,
                state.campaign_id, state.session_id, turn_id,
                tc.arguments,
            )
        except Exception:
            log.exception("Tool dispatch failed: %s (call_id=%s)", tc.name, tc.id)
            result = json.dumps({"error": f"Tool '{tc.name}' failed unexpectedly."})

        await save_message(
            state.pool, state.session_id, turn_id,
            role="tool", content=result,
            tool_name=tc.name,
            tool_data={"tool_call_id": tc.id},
        )


# -- Core Loop ----------------------------------------------------------------

async def process_turn(
    state: GameState,
    player_input: str,
) -> AsyncGenerator[ContentDelta, None]:
    """Process one player turn through the full model cycle.

    Yields ContentDelta objects as the model streams its response.
    Handles tool call round-trips internally — the caller only sees
    the final narrative content.

    Flow:
    1. Save player message
    2. Build message history
    3. Stream model response
    4. If tool calls: dispatch via _handle_tool_rounds, re-prompt
    5. If pure content: persist and finish
    """
    state.turn_id += 1
    turn_id = state.turn_id

    # Save player message
    await save_message(
        state.pool, state.session_id, turn_id,
        role="user", content=player_input,
    )

    for _round in range(MAX_TOOL_ROUNDS):
        messages = await _build_messages(state)

        # Stream and collect
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []

        async for event in stream_response(
            state.client, messages, TOOL_DEFINITIONS, state.config.model
        ):
            if isinstance(event, ContentDelta):
                content_parts.append(event.text)
                yield event
            elif isinstance(event, ToolCallRequest):
                tool_calls.append(event)

        full_content = "".join(content_parts)

        if not tool_calls:
            # Pure content — save and finish
            await save_message(
                state.pool, state.session_id, turn_id,
                role="assistant", content=full_content,
            )
            return

        # Tool calls — dispatch, persist, then loop for the follow-up
        await _handle_tool_rounds(state, turn_id, tool_calls, full_content)

    # Safety valve — shouldn't reach here in normal play
    log.warning(
        "Hit MAX_TOOL_ROUNDS (%d) for campaign=%s turn=%d",
        MAX_TOOL_ROUNDS, state.campaign_id, turn_id,
    )
    yield ContentDelta(text="\n\n*[The DM pauses, lost in thought...]*")
