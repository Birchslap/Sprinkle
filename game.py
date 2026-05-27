"""
Module: game.py
Dependencies: config.py, db.py, provider.py, tools.py, models.py

Core game loop with two-phase declarations system. Streams model responses
to the player, captures a structured [DECLARATIONS] block from each
response, then compels the model to make corresponding tool calls in a
hidden second phase. This enforces persistent record-keeping that the
model will not sustain through prompting alone.
"""

import json
import logging
from dataclasses import dataclass, field
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
DECLARATIONS_RETRIES = 1
DECLARATIONS_TAG = "[DECLARATIONS]"
DECLARATIONS_END_TAG = "[/DECLARATIONS]"
TAG_HOLDBACK = len(DECLARATIONS_TAG)

DECLARATION_TOOL_MAP = {
    "new_characters": "save_character",
    "new_locations": "save_location",
    "events": "save_event",
    "developments": "save_dm_note",
}


# -- Internal Types -----------------------------------------------------------

@dataclass
class Declaration:
    """A single category from the declarations block."""
    category: str
    items: list[str]


@dataclass
class PhaseOneResult:
    """Collects outputs from Phase 1 streaming."""
    narrative: str = ""
    declarations_raw: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: UsageData | None = None


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


# -- Declarations Parsing -----------------------------------------------------

def _split_declarations(text: str) -> tuple[str, str | None]:
    """Split narrative text from the [DECLARATIONS] block.

    Returns (clean_narrative, declarations_raw).
    If the block is malformed or absent, returns (full_text, None).
    """
    tag_start = text.find(DECLARATIONS_TAG)
    if tag_start < 0:
        return text, None

    tag_end = text.find(DECLARATIONS_END_TAG)
    if tag_end < 0:
        log.warning("Found opening [DECLARATIONS] but no closing tag")
        return text[:tag_start].rstrip(), None

    narrative = text[:tag_start].rstrip()
    raw = text[tag_start + len(DECLARATIONS_TAG):tag_end].strip()
    return narrative, raw


def _parse_declarations(raw: str) -> list[Declaration]:
    """Parse raw declarations text into structured Declaration objects.

    Expects lines like:
        new_characters: Princess Alusair, Grath the Slaver
        events: PC purchased the princess for 5000gp
    """
    declarations = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        category, _, items_str = line.partition(":")
        category = category.strip().lower().replace(" ", "_")
        items = [item.strip() for item in items_str.split(",") if item.strip()]
        if items:
            declarations.append(Declaration(category=category, items=items))
    return declarations


def _build_declaration_instruction(declarations: list[Declaration]) -> str:
    """Build a system message instructing the model to make specific tool calls."""
    lines = [
        "Complete your Record phase. Make the following tool calls based on "
        "your declarations. Respond only with tool calls, no narrative text.",
    ]
    for decl in declarations:
        tool = DECLARATION_TOOL_MAP.get(decl.category)
        if not tool:
            continue
        for item in decl.items:
            lines.append(f"- {tool} for: {item}")
    return "\n".join(lines)


# -- Phase 1: Stream Narrative + Capture Declarations -------------------------

async def _stream_phase_one(
    state: GameState,
    messages: list[dict],
    result: PhaseOneResult,
) -> AsyncGenerator[ContentDelta, None]:
    """Stream model response, yielding narrative to the player in real time.

    Captures the [DECLARATIONS] block without yielding it to the player.
    Populates the result object with narrative, declarations, tool calls,
    and usage data.

    Uses a holdback buffer to handle the [DECLARATIONS] tag spanning
    two streamed chunks — the last TAG_HOLDBACK characters are held
    until the next chunk confirms they aren't the start of the tag.
    """
    buffer = ""
    yielded_up_to = 0
    is_capturing = False

    async for event in stream_response(
        state.client, messages, TOOL_DEFINITIONS, state.config.model
    ):
        if isinstance(event, ContentDelta):
            buffer += event.text

            if not is_capturing:
                tag_pos = buffer.find(DECLARATIONS_TAG)
                if tag_pos >= 0:
                    # Found the tag — yield everything before it
                    remaining = buffer[yielded_up_to:tag_pos]
                    if remaining:
                        yield ContentDelta(text=remaining)
                    yielded_up_to = tag_pos
                    is_capturing = True
                else:
                    # Yield up to holdback to handle tag spanning chunks
                    safe_end = len(buffer) - TAG_HOLDBACK
                    if safe_end > yielded_up_to:
                        chunk = buffer[yielded_up_to:safe_end]
                        yield ContentDelta(text=chunk)
                        yielded_up_to = safe_end

        elif isinstance(event, ToolCallRequest):
            result.tool_calls.append(event)
        elif isinstance(event, UsageData):
            result.usage = event

    # Stream ended — flush remaining narrative if no declarations found
    if not is_capturing and yielded_up_to < len(buffer):
        yield ContentDelta(text=buffer[yielded_up_to:])

    # Split the final buffer into narrative and declarations
    narrative, declarations_raw = _split_declarations(buffer)
    result.narrative = narrative
    result.declarations_raw = declarations_raw


# -- Phase 2: Compel Records -------------------------------------------------

async def _execute_declarations(
    state: GameState,
    turn_id: int,
    declarations: list[Declaration],
) -> bool:
    """Force the model to make tool calls for declared items.

    Injects a system message with specific instructions, collects tool
    calls, dispatches them. Returns True if any tool calls were made.
    Retries once with a pointed reminder on failure.
    """
    instruction = _build_declaration_instruction(declarations)

    for attempt in range(1 + DECLARATIONS_RETRIES):
        messages = await _build_messages(state)
        messages.append({"role": "system", "content": instruction})

        tool_calls: list[ToolCallRequest] = []

        async for event in stream_response(
            state.client, messages, TOOL_DEFINITIONS, state.config.model
        ):
            if isinstance(event, ToolCallRequest):
                tool_calls.append(event)
            # Ignore content and usage — Phase 2 is tool-only

        if tool_calls:
            # Save the assistant message with tool calls
            assistant_msg = build_assistant_tool_call_message(tool_calls)
            await save_message(
                state.pool, state.session_id, turn_id,
                role="assistant", content="",
                tool_data=assistant_msg,
            )

            # Dispatch each tool call and save results
            for tc in tool_calls:
                try:
                    tool_result = await dispatch_tool(
                        tc.name, state.pool,
                        state.campaign_id, state.session_id, turn_id,
                        tc.arguments,
                    )
                except Exception:
                    log.exception(
                        "Phase 2 tool dispatch failed: %s (call_id=%s)",
                        tc.name, tc.id,
                    )
                    tool_result = json.dumps({
                        "error": f"Tool '{tc.name}' failed unexpectedly."
                    })

                await save_message(
                    state.pool, state.session_id, turn_id,
                    role="tool", content=tool_result,
                    tool_name=tc.name,
                    tool_data={"tool_call_id": tc.id},
                )

            log.info(
                "Phase 2 complete: %d tool calls dispatched (attempt %d)",
                len(tool_calls), attempt + 1,
            )
            return True

        log.warning(
            "Phase 2 attempt %d: model returned no tool calls", attempt + 1,
        )

        # Retry with a more pointed instruction
        instruction = (
            "You did not make any tool calls. You MUST call the tools listed "
            "above to record your declarations. Make the tool calls now. "
            "No narrative text."
        )

    log.error("Phase 2 failed after %d attempts", 1 + DECLARATIONS_RETRIES)
    return False


# -- Tool Dispatch (Phase 1 Recall Rounds) ------------------------------------

async def _handle_tool_rounds(
    state: GameState,
    turn_id: int,
    tool_calls: list[ToolCallRequest],
    content: str,
) -> None:
    """Dispatch tool calls from Phase 1 (Recall rounds).

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
    """Process one player turn through the two-phase declarations system.

    Phase 1: Stream narrative to the player while handling Recall tool
             rounds. Captures the [DECLARATIONS] block from the final
             narrative response.
    Phase 2: Compel the model to make Record tool calls based on its
             declarations. Hidden from the player.

    Yields ContentDelta objects for the player-visible narrative only.
    """
    state.turn_id += 1
    turn_id = state.turn_id

    # Save player message
    await save_message(
        state.pool, state.session_id, turn_id,
        role="user", content=player_input,
    )

    # -- Phase 1: Recall rounds + narrative + declarations --------------------

    result = PhaseOneResult()

    for _round in range(MAX_TOOL_ROUNDS):
        messages = await _build_messages(state)

        result = PhaseOneResult()
        async for delta in _stream_phase_one(state, messages, result):
            yield delta

        # Persist token usage
        if result.usage:
            try:
                await save_token_usage(
                    state.pool, state.session_id, turn_id,
                    result.usage.prompt_tokens,
                    result.usage.completion_tokens,
                    result.usage.cached_tokens,
                    result.usage.total_tokens,
                )
            except Exception:
                log.exception("Failed to save token usage for turn %d", turn_id)

        if result.tool_calls and not result.narrative:
            # Pure tool round (Recall phase) — dispatch and loop
            await _handle_tool_rounds(state, turn_id, result.tool_calls, "")
            continue

        if result.tool_calls and result.narrative:
            # Mixed: tool calls alongside narrative
            await _handle_tool_rounds(
                state, turn_id, result.tool_calls, result.narrative,
            )
            break

        # Pure narrative (possibly with declarations) — save and proceed
        await save_message(
            state.pool, state.session_id, turn_id,
            role="assistant", content=result.narrative,
        )
        break

    else:
        # Safety valve — hit MAX_TOOL_ROUNDS
        log.warning(
            "Hit MAX_TOOL_ROUNDS (%d) for campaign=%s turn=%d",
            MAX_TOOL_ROUNDS, state.campaign_id, turn_id,
        )
        yield ContentDelta(text="\n\n*[The DM pauses, lost in thought...]*")
        return

    # -- Phase 2: Compel records from declarations ----------------------------

    if result.declarations_raw:
        declarations = _parse_declarations(result.declarations_raw)
        if declarations:
            log.info(
                "Declarations captured: %s",
                {d.category: d.items for d in declarations},
            )
            await _execute_declarations(state, turn_id, declarations)
        else:
            log.warning("Declarations block present but could not be parsed")
    else:
        log.info("No declarations block in response")
