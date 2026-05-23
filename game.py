"""
Module: game.py
Dependencies: config.py, db.py, provider.py, tools.py, types.py

Core game loop. Manages conversation state, streams model responses,
dispatches tool calls, and persists every message to the transcript.
"""

import json
from typing import AsyncGenerator

import asyncpg

from config import AppConfig
from db import (
    create_campaign, create_session, end_session,
    save_message, get_messages,
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
                             setting: str = None) -> dict:
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
        # Recover turn_id from the most recent message in any session
        rows = await self.pool.fetch(
            """SELECT turn_id FROM messages
               WHERE session_id IN (
                   SELECT id FROM sessions WHERE campaign_id = $1
               )
               ORDER BY created_at DESC LIMIT 1""",
            self.campaign_id
        )
        self.turn_id = rows[0]["turn_id"] if rows else 0
        return {"campaign_id": campaign_id, "session_id": session["id"]}

    async def end(self, summary: str = None) -> None:
        """End the current session."""
        if self.session_id:
            await end_session(self.pool, self.session_id, summary)


# -- Message Building ---------------------------------------------------------

async def _build_messages(state: GameState) -> list[dict]:
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


# -- Core Loop ----------------------------------------------------------------

async def process_turn(state: GameState,
                       player_input: str) -> AsyncGenerator[ContentDelta, None]:
    """Process one player turn through the full model cycle.

    Yields ContentDelta objects as the model streams its response.
    Handles tool call round-trips internally — the caller only sees
    the final narrative content.

    Flow:
    1. Save player message
    2. Build message history
    3. Stream model response
    4. If tool calls: dispatch, persist, re-prompt (repeat)
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
        content_parts = []
        tool_calls = []

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
            # Pure content response — save and we're done
            await save_message(
                state.pool, state.session_id, turn_id,
                role="assistant", content=full_content,
            )
            return

        # Tool calls — save the assistant's tool call message
        assistant_msg = build_assistant_tool_call_message(tool_calls)
        await save_message(
            state.pool, state.session_id, turn_id,
            role="assistant", content=full_content or "",
            tool_data=assistant_msg,
        )

        # Dispatch each tool call and save results
        for tc in tool_calls:
            result = await dispatch_tool(
                tc.name, state.pool,
                state.campaign_id, state.session_id, turn_id,
                tc.arguments,
            )
            await save_message(
                state.pool, state.session_id, turn_id,
                role="tool", content=result,
                tool_name=tc.name,
                tool_data={"tool_call_id": tc.id},
            )

        # Loop back — the model will see its tool calls and results
        # in the history and generate a follow-up response

    # Safety valve — shouldn't reach here in normal play
    yield ContentDelta(
        text="\n\n*[The DM pauses, lost in thought...]*"
    )
