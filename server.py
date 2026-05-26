"""
Module: server.py
Dependencies: config.py, db.py, game.py, prompts.py

FastAPI application. Manages the connection pool lifecycle,
serves the frontend, and exposes WebSocket and REST endpoints
for running the game.
"""

import csv
import io
import json
import logging
import sys
from contextlib import asynccontextmanager

# -- Logging ------------------------------------------------------------------
# Configure root logger so every module's log output is visible in the terminal.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
# Quiet down noisy libraries
logging.getLogger("asyncpg").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

import asyncpg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from config import load_config
from db import (create_campaign, delete_campaign, get_campaign,
               get_campaign_usage_detail, get_chat_export,
               get_message_history, list_campaigns)
from game import GameState, process_turn
from prompts import build_system_prompt

log = logging.getLogger(__name__)


# -- App Setup ----------------------------------------------------------------

config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the connection pool across the app's lifetime."""
    app.state.pool = await asyncpg.create_pool(config.database.url)
    yield
    await app.state.pool.close()


app = FastAPI(title="Sprinkle", version="0.1.0", lifespan=lifespan)


# -- Static Files & Frontend --------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def _index():
    return FileResponse("static/index.html")


# -- REST Endpoints -----------------------------------------------------------

@app.get("/api/campaigns")
async def _list_campaigns(request: Request):
    """List all campaigns."""
    rows = await list_campaigns(request.app.state.pool)
    return {"campaigns": rows}


@app.post("/api/campaigns")
async def _create_campaign(request: Request, body: dict):
    """Create a new campaign."""
    name = body.get("name", "Untitled Campaign")
    setting = body.get("setting")
    character_doc = body.get("character_doc")
    campaign = await create_campaign(
        request.app.state.pool, name, setting, character_doc,
    )
    return {"campaign": campaign}


@app.get("/api/campaigns/{campaign_id}")
async def _get_campaign(request: Request, campaign_id: int):
    """Get a single campaign."""
    campaign = await get_campaign(request.app.state.pool, campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}
    return {"campaign": campaign}


@app.delete("/api/campaigns/{campaign_id}")
async def _delete_campaign(request: Request, campaign_id: int):
    """Delete a campaign and all associated data."""
    deleted = await delete_campaign(request.app.state.pool, campaign_id)
    if not deleted:
        return {"error": "Campaign not found"}
    return {"status": "deleted"}


# -- Export Endpoints ---------------------------------------------------------

@app.get("/api/campaigns/{campaign_id}/usage.csv")
async def _export_usage(request: Request, campaign_id: int):
    """Download token usage as CSV."""
    rows = await get_campaign_usage_detail(request.app.state.pool, campaign_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "session_id", "turn_id", "prompt_tokens", "completion_tokens",
        "cached_tokens", "total_tokens", "cache_hit_pct", "timestamp",
    ])
    for r in rows:
        writer.writerow([
            r["session_id"], r["turn_id"], r["prompt_tokens"],
            r["completion_tokens"], r["cached_tokens"], r["total_tokens"],
            r["cache_hit_pct"], r["created_at"].isoformat(),
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_usage.csv"},
    )


@app.get("/api/campaigns/{campaign_id}/chat.md")
async def _export_chat(request: Request, campaign_id: int):
    """Download chat transcript as markdown."""
    pool = request.app.state.pool
    campaign = await get_campaign(pool, campaign_id)
    messages = await get_chat_export(pool, campaign_id)

    lines = []
    name = campaign["name"] if campaign else f"Campaign {campaign_id}"
    lines.append(f"# {name} — Chat Transcript\n")

    current_turn = None
    for msg in messages:
        if msg["turn_id"] != current_turn:
            current_turn = msg["turn_id"]
            lines.append(f"\n---\n")

        label = "**Player**" if msg["role"] == "user" else "**DM**"
        lines.append(f"{label}: {msg['content']}\n")

    return Response(
        content="\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name} transcript.md"'},
    )


# -- WebSocket Game Endpoint --------------------------------------------------

@app.websocket("/ws/game/{campaign_id}")
async def _game_socket(ws: WebSocket, campaign_id: int):
    """WebSocket endpoint for a game session.

    Protocol:
        Client sends: {"type": "message", "content": "I search the room"}
        Server sends: {"type": "delta", "text": "..."} — streamed content
        Server sends: {"type": "turn_end"} — signals end of DM response
        Server sends: {"type": "error", "message": "..."} — on failure

    First message after connect can be:
        {"type": "start", "name": "My Campaign", "setting": "..."} — new campaign
        {"type": "resume"} — resume existing campaign
    """
    await ws.accept()

    pool = ws.app.state.pool
    state = GameState(pool, config)

    try:
        # Wait for initialisation message
        init_raw = await ws.receive_text()
        init = json.loads(init_raw)

        campaign = await get_campaign(pool, campaign_id)
        if not campaign:
            await ws.send_json({"type": "error", "message": "Campaign not found"})
            await ws.close()
            return

        system_prompt = build_system_prompt(
            campaign_name=campaign["name"],
            setting=campaign.get("setting", ""),
            character_doc=campaign.get("character_doc"),
        )

        if init.get("type") == "start":
            await state.start_campaign(campaign_id, system_prompt)
        else:
            await state.resume_campaign(campaign_id, system_prompt)

            # Send chat history so the player sees where they left off
            history = await get_message_history(
                pool, campaign_id, config.history_limit,
            )
            for msg in history:
                await ws.send_json({
                    "type": "history",
                    "role": msg["role"],
                    "content": msg["content"],
                })
            await ws.send_json({"type": "history_end"})

        await ws.send_json({"type": "ready", "campaign_id": campaign_id})

        # Game loop
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg.get("type") != "message":
                continue

            content = msg.get("content", "").strip()
            if not content:
                continue

            try:
                async for delta in process_turn(state, content):
                    await ws.send_json({
                        "type": "delta",
                        "text": delta.text,
                    })
                await ws.send_json({"type": "turn_end"})
            except Exception as e:
                log.exception("Turn failed for campaign %d", campaign_id)
                await ws.send_json({
                    "type": "error",
                    "message": f"Turn failed: {str(e)}",
                })

    except WebSocketDisconnect:
        await state.end(summary="Player disconnected")
    except Exception as e:
        log.exception("WebSocket error for campaign %d", campaign_id)
        try:
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.close()
        except Exception:
            pass
        await state.end(summary=f"Session ended by error: {str(e)}")
