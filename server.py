"""
Module: server.py
Dependencies: config.py, db.py, game.py, prompts.py

FastAPI application. Manages the connection pool lifecycle,
serves the frontend, and exposes WebSocket and REST endpoints
for running the game.
"""

import json
import asyncio

import asyncpg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import load_config
from db import create_campaign, list_campaigns, get_campaign
from game import GameState, process_turn
from prompts import build_system_prompt


# -- App Setup ----------------------------------------------------------------

config = load_config()
app = FastAPI(title="Sprinkle", version="0.1.0")

# Connection pool — created on startup, closed on shutdown
pool: asyncpg.Pool | None = None


@app.on_event("startup")
async def _startup():
    global pool
    pool = await asyncpg.create_pool(config.database.url)


@app.on_event("shutdown")
async def _shutdown():
    if pool:
        await pool.close()


# -- Static Files & Frontend --------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def _index():
    return FileResponse("static/index.html")


# -- REST Endpoints -----------------------------------------------------------

@app.get("/api/campaigns")
async def _list_campaigns():
    """List all campaigns."""
    rows = await list_campaigns(pool)
    return {"campaigns": [dict(r) for r in rows]}


@app.post("/api/campaigns")
async def _create_campaign(body: dict):
    """Create a new campaign."""
    name = body.get("name", "Untitled Campaign")
    setting = body.get("setting")
    campaign = await create_campaign(pool, name, setting)
    return {"campaign": dict(campaign)}


@app.get("/api/campaigns/{campaign_id}")
async def _get_campaign(campaign_id: int):
    """Get a single campaign."""
    campaign = await get_campaign(pool, campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}
    return {"campaign": dict(campaign)}


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
        )

        if init.get("type") == "start":
            await state.start_campaign(
                campaign["name"], system_prompt, campaign.get("setting"),
            )
        else:
            await state.resume_campaign(campaign_id, system_prompt)

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
                await ws.send_json({
                    "type": "error",
                    "message": f"Turn failed: {str(e)}",
                })

    except WebSocketDisconnect:
        await state.end(summary="Player disconnected")
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.close()
        except Exception:
            pass
        await state.end(summary=f"Session ended by error: {str(e)}")
