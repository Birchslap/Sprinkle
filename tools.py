"""
Module: tools.py
Dependencies: db.py

Tool definitions and handlers for the DM model.
Defines what tools the model can call and how each call is executed.
"""

import json
import logging
import random
import re
from typing import Any

import asyncpg

from db import (
    get_character,
    get_dm_note,
    get_location,
    get_messages_by_turn,
    list_characters,
    list_dm_notes,
    list_locations,
    save_character,
    save_dm_note,
    save_event,
    save_location,
    update_dm_note,
)
from db import search_rules, update_character_status

log = logging.getLogger(__name__)


# ============================================================
# Dice Rolling
# ============================================================

def _parse_dice(expression: str) -> dict:
    """Parse a dice expression like '2d6+3' into components."""
    expression = expression.lower().replace(" ", "")
    match = re.match(r'^(\d+)d(\d+)([+-]\d+)?$', expression)
    if not match:
        raise ValueError(f"Invalid dice expression: {expression}")
    return {
        "count": int(match.group(1)),
        "sides": int(match.group(2)),
        "modifier": int(match.group(3)) if match.group(3) else 0,
    }


def roll_dice(expression: str) -> dict:
    """Roll dice and return individual rolls, modifier, and total."""
    parsed = _parse_dice(expression)
    rolls = [random.randint(1, parsed["sides"]) for _ in range(parsed["count"])]
    total = sum(rolls) + parsed["modifier"]
    return {
        "expression": expression,
        "rolls": rolls,
        "modifier": parsed["modifier"],
        "total": total,
    }


# ============================================================
# Tool Definitions (OpenAI function calling format)
# ============================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll dice using standard notation. Examples: '1d20', '2d6+3', '4d6-1'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Dice expression in NdS+M format.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_character",
            "description": (
                "Save or update a character (PC or NPC). "
                "If a character with this name already exists in the campaign, "
                "it will be updated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Character name."},
                    "character_type": {
                        "type": "string",
                        "enum": ["pc", "npc"],
                        "description": "Player character or NPC.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Appearance, personality, role in the world.",
                    },
                    "stats": {
                        "type": "object",
                        "description": (
                            "Character statistics — abilities, HP, AC, etc. "
                            "Freeform JSON."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": "Private DM notes about this character.",
                    },
                },
                "required": ["name", "character_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_character",
            "description": "Retrieve full details of a character by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Character name to look up.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_characters",
            "description": (
                "List all characters in the campaign. Returns names, types, "
                "and status without full details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "character_type": {
                        "type": "string",
                        "enum": ["pc", "npc"],
                        "description": "Filter by type. Omit to list all.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_character_status",
            "description": (
                "Update a character's status. Use this when a character "
                "dies, goes missing, retires, or otherwise changes "
                "their availability in the narrative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Character name.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "dead", "missing", "retired", "inactive"],
                        "description": "New status for the character.",
                    },
                },
                "required": ["name", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_location",
            "description": (
                "Save or update a location. "
                "If a location with this name already exists in the campaign, "
                "it will be updated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Location name."},
                    "description": {
                        "type": "string",
                        "description": (
                            "What this place looks like, what it contains, "
                            "who lives here."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": "Private DM notes about this location.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_location",
            "description": "Retrieve full details of a location by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Location name to look up.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_locations",
            "description": (
                "List all locations in the campaign. Returns names and status "
                "without full details."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_event",
            "description": (
                "Record a significant narrative event that shapes the campaign."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what happened.",
                    },
                    "details": {
                        "type": "string",
                        "description": "Full description of the event.",
                    },
                    "significance": {
                        "type": "string",
                        "description": "Why this event matters for the campaign.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_dm_note",
            "description": (
                "Save a private DM note. Use categories like 'plot_plan', "
                "'intention', 'secret', 'foreshadowing', 'observation', "
                "'npc_motivation', or any other useful label."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Note category for organisation.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short descriptive title.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The note content.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Why you are making this plan or observation. "
                            "Your thinking behind the decision."
                        ),
                    },
                },
                "required": ["category", "title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dm_note",
            "description": (
                "Retrieve the full content of a specific DM note by its ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "integer",
                        "description": "The note ID.",
                    }
                },
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dm_notes",
            "description": (
                "Browse DM notes. Returns titles, categories, and status "
                "without full content. Filter by category and/or status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "Filter by category. Omit to list all."
                        ),
                    },
                    "status": {
                        "type": "string",
                        "description": (
                            "Filter by status (active, resolved, abandoned). "
                            "Omit to list all."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_dm_note",
            "description": (
                "Update an existing DM note. Can change content, reasoning, "
                "or status independently."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "integer",
                        "description": "The note ID to update.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "New content. Omit to leave unchanged."
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "New reasoning. Omit to leave unchanged."
                        ),
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "resolved", "abandoned"],
                        "description": (
                            "New status. Omit to leave unchanged."
                        ),
                    },
                },
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_rules",
            "description": (
                "Search the D&D 5E rules reference for authoritative information. "
                "Use this to look up monster stat blocks, spell descriptions, "
                "race traits, class features, items, feats, conditions, and "
                "any other rules content. Always prefer this over memory for "
                "specific mechanical details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms (e.g., 'firbolg', 'fireball', 'beholder').",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "monster", "spell", "race", "class", "subclass",
                            "item", "feat", "background", "condition",
                        ],
                        "description": "Filter by category. Omit to search all.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_turn_context",
            "description": (
                "Retrieve all messages from a specific turn. "
                "Use this to review what was happening when a note or event "
                "was created."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "turn_id": {
                        "type": "integer",
                        "description": "The turn number to retrieve.",
                    }
                },
                "required": ["turn_id"],
            },
        },
    },
]


# ============================================================
# Tool Handlers
# ============================================================

async def _handle_roll_dice(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    return roll_dice(args["expression"])


async def _handle_save_character(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    result = await save_character(
        pool, campaign_id,
        name=args["name"],
        character_type=args["character_type"],
        description=args.get("description"),
        stats=args.get("stats"),
        notes=args.get("notes"),
    )
    return {"saved": args["name"], "id": result["id"]}


async def _handle_get_character(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    row = await get_character(pool, campaign_id, args["name"])
    if not row:
        return {"error": f"No character named '{args['name']}' found."}
    return row


async def _handle_list_characters(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    rows = await list_characters(
        pool, campaign_id, character_type=args.get("character_type"),
    )
    return {"characters": rows}


async def _handle_update_character_status(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    await update_character_status(
        pool, campaign_id, name=args["name"], status=args["status"],
    )
    return {"updated": args["name"], "status": args["status"]}


async def _handle_save_location(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    result = await save_location(
        pool, campaign_id,
        name=args["name"],
        description=args.get("description"),
        notes=args.get("notes"),
    )
    return {"saved": args["name"], "id": result["id"]}


async def _handle_get_location(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    row = await get_location(pool, campaign_id, args["name"])
    if not row:
        return {"error": f"No location named '{args['name']}' found."}
    return row


async def _handle_list_locations(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    rows = await list_locations(pool, campaign_id)
    return {"locations": rows}


async def _handle_save_event(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    result = await save_event(
        pool, campaign_id, session_id, turn_id,
        summary=args["summary"],
        details=args.get("details"),
        significance=args.get("significance"),
    )
    return {"saved": args["summary"], "id": result["id"]}


async def _handle_save_dm_note(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    result = await save_dm_note(
        pool, campaign_id, session_id, turn_id,
        category=args["category"],
        title=args["title"],
        content=args["content"],
        reasoning=args.get("reasoning"),
    )
    return {"saved": args["title"], "id": result["id"]}


async def _handle_get_dm_note(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    row = await get_dm_note(pool, args["note_id"])
    if not row:
        return {"error": f"No note with ID {args['note_id']} found."}
    return row


async def _handle_list_dm_notes(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    rows = await list_dm_notes(
        pool, campaign_id,
        category=args.get("category"),
        status=args.get("status"),
    )
    return {"notes": rows}


async def _handle_update_dm_note(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    result = await update_dm_note(
        pool, args["note_id"],
        content=args.get("content"),
        reasoning=args.get("reasoning"),
        status=args.get("status"),
    )
    return {"updated": args["note_id"]}


async def _handle_search_rules(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    rows = await search_rules(
        pool, query=args["query"], category=args.get("category"),
    )
    if not rows:
        return {"results": [], "message": f"No rules entries found for '{args['query']}'."}
    return {"results": [{"name": r["name"], "category": r["category"],
                          "source": r["source"], "content": r["content"]}
                         for r in rows]}


async def _handle_get_turn_context(
    pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
    args: dict[str, Any],
) -> dict:
    rows = await get_messages_by_turn(pool, session_id, args["turn_id"])
    return {"turn_id": args["turn_id"], "messages": rows}


# ============================================================
# Dispatch
# ============================================================

_HANDLERS = {
    "roll_dice": _handle_roll_dice,
    "save_character": _handle_save_character,
    "get_character": _handle_get_character,
    "list_characters": _handle_list_characters,
    "update_character_status": _handle_update_character_status,
    "save_location": _handle_save_location,
    "get_location": _handle_get_location,
    "list_locations": _handle_list_locations,
    "save_event": _handle_save_event,
    "save_dm_note": _handle_save_dm_note,
    "get_dm_note": _handle_get_dm_note,
    "list_dm_notes": _handle_list_dm_notes,
    "update_dm_note": _handle_update_dm_note,
    "search_rules": _handle_search_rules,
    "get_turn_context": _handle_get_turn_context,
}


async def dispatch_tool(
    name: str, pool: asyncpg.Pool, campaign_id: int,
    session_id: int, turn_id: int, args: dict[str, Any],
) -> str:
    """Route a tool call to its handler and return a JSON result string."""
    handler = _HANDLERS.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        result = await handler(pool, campaign_id, session_id, turn_id, args)
        return json.dumps(result, default=str)
    except Exception as e:
        log.exception("Tool '%s' failed with args %s", name, args)
        return json.dumps({"error": str(e)})
