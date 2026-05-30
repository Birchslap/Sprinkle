"""
Module: game.py
Dependencies: config.py, db.py, provider.py, tools.py, models.py

Core game loop. Streams model responses to the player and dispatches
tool calls the model makes voluntarily. With reasoning_effort set to
high, the model saves characters, locations, events, and DM notes
through native tool use — no coercion required.
"""

import json
import logging
from typing import AsyncGenerator

import asyncpg

from config import AppConfig
from db import (
    create_session, end_session,
    save_message, get_campaign_messages, get_last_turn_id,
    get_message_history, save_token_usage,
)
from provider import (
    create_client, stream_response,
    build_assistant_tool_call_message, build_tool_result_message,
)
from tools import TOOL_DEFINITIONS, dispatch_tool
from models import ContentDelta, ToolCallRequest, UsageData

log = logging.getLogger(__name__)


# -- Constants ----------------------------------------------------------------

MAX_TOOL_ROUNDS = 15


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

    async def start_campaign(self, campaign_id: int, system_prompt: str) -> None:
        """Start a new session for an existing campaign."""
        self.campaign_id = campaign_id
        self.system_prompt = system_prompt
        session = await create_session(self.pool, self.campaign_id)
        self.session_id = session["id"]
        self.turn_id = 0

    async def get_history(self) -> list[dict]:
        """Retrieve player-visible chat history for the current campaign."""
        return await get_message_history(
            self.pool, self.campaign_id, self.config.history_limit
        )

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

async def _build_messages(state: GameState) -> list[dict[str, str | dict]]:
    """Build the message list for the API call.

    Uses increment-and-chop for cache-friendly context management:
    - Messages accumulate up to message_window_max (default 150).
    - When the ceiling is hit, only the most recent message_window_chop
      (default 50) are kept.
    - Between chops the message prefix is stable, enabling API-level
      prompt caching across consecutive turns.
    """
    messages = [{"role": "system", "content": state.system_prompt}]

    cfg = state.config
    rows = await get_campaign_messages(
        state.pool, state.campaign_id, limit=cfg.message_window_max
    )

    if len(rows) >= cfg.message_window_max:
        rows = rows[:cfg.message_window_chop]

    rows.reverse()

    for row in rows:
        if row.get("tool_name"):
            tool_data = row.get("tool_data") or {}
            messages.append({
                "role": "tool",
                "tool_call_id": tool_data.get("tool_call_id", ""),
                "content": row["content"],
            })
        elif row["role"] == "assistant" and row.get("tool_data"):
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
    """Dispatch tool calls from the model's response.

    Saves the assistant's tool-call message, dispatches each call,
    and saves results. Errors are logged but don't crash the turn.
    """
    assistant_msg = build_assistant_tool_call_message(tool_calls)
    await save_message(
        state.pool, state.session_id, turn_id,
        role="assistant", content=content or "",
        tool_data=assistant_msg,
    )

    for tc in tool_calls:
        try:
            result = await dispatch_tool(
                tc.name, state.pool,
                state.campaign_id, state.session_id, turn_id,
                tc.arguments,
            )
        except Exception:
            log.exception(
                "Tool dispatch failed: %s (call_id=%s)", tc.name, tc.id,
            )
            result = json.dumps({
                "error": f"Tool '{tc.name}' failed unexpectedly."
            })

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
    """Process one player turn.

    Streams the model's narrative response to the player while handling
    any tool calls the model makes voluntarily. Tool rounds loop until
    the model produces a final narrative response.

    Yields ContentDelta objects for the player-visible narrative.
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

        narrative = ""
        tool_calls: list[ToolCallRequest] = []
        usage: UsageData | None = None

        async for event in stream_response(
            state.client, messages, TOOL_DEFINITIONS, state.config.model
        ):
            if isinstance(event, ContentDelta):
                narrative += event.text
                yield event
            elif isinstance(event, ToolCallRequest):
                tool_calls.append(event)
            elif isinstance(event, UsageData):
                usage = event

        # Persist token usage
        if usage:
            try:
                await save_token_usage(
                    state.pool, state.session_id, turn_id,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.cached_tokens,
                    usage.total_tokens,
                )
            except Exception:
                log.exception("Failed to save token usage for turn %d", turn_id)

        if tool_calls and not narrative:
            # Pure tool round — dispatch and loop for the next response
            await _handle_tool_rounds(state, turn_id, tool_calls, "")
            continue

        if tool_calls and narrative:
            # Mixed: tool calls alongside narrative — dispatch and finish
            await _handle_tool_rounds(state, turn_id, tool_calls, narrative)
            break

        # Pure narrative — save and finish
        await save_message(
            state.pool, state.session_id, turn_id,
            role="assistant", content=narrative,
        )
        break

    else:
        # Safety valve — hit MAX_TOOL_ROUNDS
        log.warning(
            "Hit MAX_TOOL_ROUNDS (%d) for campaign=%s turn=%d",
            MAX_TOOL_ROUNDS, state.campaign_id, turn_id,
        )
        yield ContentDelta(text="\n\n*[The DM pauses, lost in thought...]*")
