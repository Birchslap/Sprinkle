"""
Module: db.py
Dependencies: none (asyncpg is third-party)

Database connection pool and query functions for all Sprinkle tables.
All functions take the pool as their first argument. No global state.
"""

import json
import asyncpg


# -- Pool Management ----------------------------------------------------------

async def create_pool(database_url: str) -> asyncpg.Pool:
    """Create and return a connection pool."""
    return await asyncpg.create_pool(database_url)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close the connection pool."""
    await pool.close()


# -- Campaigns ----------------------------------------------------------------

async def create_campaign(pool: asyncpg.Pool, name: str, setting: str | None = None) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO campaigns (name, setting)
           VALUES ($1, $2)
           RETURNING *""",
        name, setting
    )
    return dict(row)


async def get_campaign(pool: asyncpg.Pool, campaign_id: int) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM campaigns WHERE id = $1", campaign_id
    )
    return dict(row) if row else None


async def update_campaign_status(pool: asyncpg.Pool, campaign_id: int, status: str) -> None:
    await pool.execute(
        """UPDATE campaigns SET status = $1, updated_at = now()
           WHERE id = $2""",
        status, campaign_id
    )


# -- Sessions -----------------------------------------------------------------

async def create_session(pool: asyncpg.Pool, campaign_id: int) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO sessions (campaign_id)
           VALUES ($1)
           RETURNING *""",
        campaign_id
    )
    return dict(row)


async def end_session(pool: asyncpg.Pool, session_id: int, summary: str | None = None) -> None:
    await pool.execute(
        """UPDATE sessions SET ended_at = now(), summary = $1
           WHERE id = $2""",
        summary, session_id
    )


async def get_session(pool: asyncpg.Pool, session_id: int) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM sessions WHERE id = $1", session_id
    )
    return dict(row) if row else None


# -- Messages -----------------------------------------------------------------

async def save_message(pool: asyncpg.Pool, session_id: int, turn_id: int, role: str,
                       content: str, tool_name: str | None = None,
                       tool_data: dict | None = None) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO messages (session_id, turn_id, role, content,
                                tool_name, tool_data)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)
           RETURNING *""",
        session_id, turn_id, role, content, tool_name,
        json.dumps(tool_data) if tool_data else None
    )
    return dict(row)


async def get_messages(pool: asyncpg.Pool, session_id: int, limit: int = 50) -> list[dict]:
    """Most recent messages first. Flip order in the caller if needed."""
    rows = await pool.fetch(
        """SELECT * FROM messages
           WHERE session_id = $1
           ORDER BY created_at DESC
           LIMIT $2""",
        session_id, limit
    )
    return [dict(r) for r in rows]


async def get_messages_by_turn(pool: asyncpg.Pool, session_id: int,
                               turn_id: int) -> list[dict]:
    """All messages from a specific turn, in order."""
    rows = await pool.fetch(
        """SELECT * FROM messages
           WHERE session_id = $1 AND turn_id = $2
           ORDER BY created_at""",
        session_id, turn_id
    )
    return [dict(r) for r in rows]


# -- Characters ---------------------------------------------------------------

async def save_character(pool: asyncpg.Pool, campaign_id: int, name: str,
                         character_type: str, description: str | None = None,
                         stats: dict | None = None, notes: str | None = None) -> dict:
    """Upsert: creates or updates by (campaign_id, name)."""
    row = await pool.fetchrow(
        """INSERT INTO characters
               (campaign_id, name, character_type, description, stats, notes)
           VALUES ($1, $2, $3, $4, $5::jsonb, $6)
           ON CONFLICT (campaign_id, name)
           DO UPDATE SET character_type = EXCLUDED.character_type,
                         description = EXCLUDED.description,
                         stats = EXCLUDED.stats,
                         notes = EXCLUDED.notes,
                         updated_at = now()
           RETURNING *""",
        campaign_id, name, character_type, description,
        json.dumps(stats) if stats else None, notes
    )
    return dict(row)


async def get_character(pool: asyncpg.Pool, campaign_id: int, name: str) -> dict | None:
    row = await pool.fetchrow(
        """SELECT * FROM characters
           WHERE campaign_id = $1 AND name = $2""",
        campaign_id, name
    )
    return dict(row) if row else None


async def list_characters(pool: asyncpg.Pool, campaign_id: int,
                          character_type: str | None = None,
                          status: str = "active") -> list[dict]:
    """Browse layer: names and metadata, no full stats blob."""
    if character_type:
        rows = await pool.fetch(
            """SELECT id, name, character_type, status, updated_at
               FROM characters
               WHERE campaign_id = $1 AND character_type = $2 AND status = $3
               ORDER BY name""",
            campaign_id, character_type, status
        )
    else:
        rows = await pool.fetch(
            """SELECT id, name, character_type, status, updated_at
               FROM characters
               WHERE campaign_id = $1 AND status = $2
               ORDER BY name""",
            campaign_id, status
        )
    return [dict(r) for r in rows]


async def update_character_status(pool: asyncpg.Pool, campaign_id: int, name: str,
                                  status: str) -> None:
    await pool.execute(
        """UPDATE characters SET status = $1, updated_at = now()
           WHERE campaign_id = $2 AND name = $3""",
        status, campaign_id, name
    )


# -- Locations ----------------------------------------------------------------

async def save_location(pool: asyncpg.Pool, campaign_id: int, name: str,
                        description: str | None = None,
                        notes: str | None = None) -> dict:
    """Upsert: creates or updates by (campaign_id, name)."""
    row = await pool.fetchrow(
        """INSERT INTO locations (campaign_id, name, description, notes)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (campaign_id, name)
           DO UPDATE SET description = EXCLUDED.description,
                         notes = EXCLUDED.notes,
                         updated_at = now()
           RETURNING *""",
        campaign_id, name, description, notes
    )
    return dict(row)


async def get_location(pool: asyncpg.Pool, campaign_id: int, name: str) -> dict | None:
    row = await pool.fetchrow(
        """SELECT * FROM locations
           WHERE campaign_id = $1 AND name = $2""",
        campaign_id, name
    )
    return dict(row) if row else None


async def list_locations(pool: asyncpg.Pool, campaign_id: int,
                         status: str = "active") -> list[dict]:
    """Browse layer: names and metadata only."""
    rows = await pool.fetch(
        """SELECT id, name, status, updated_at
           FROM locations
           WHERE campaign_id = $1 AND status = $2
           ORDER BY name""",
        campaign_id, status
    )
    return [dict(r) for r in rows]


# -- Events -------------------------------------------------------------------

async def save_event(pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
                     summary: str, details: str | None = None,
                     significance: str | None = None) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO events
               (campaign_id, session_id, turn_id, summary, details, significance)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *""",
        campaign_id, session_id, turn_id, summary, details, significance
    )
    return dict(row)


async def get_events(pool: asyncpg.Pool, campaign_id: int, limit: int = 50) -> list[dict]:
    rows = await pool.fetch(
        """SELECT * FROM events
           WHERE campaign_id = $1
           ORDER BY created_at DESC
           LIMIT $2""",
        campaign_id, limit
    )
    return [dict(r) for r in rows]


# -- DM Notes -----------------------------------------------------------------

async def save_dm_note(pool: asyncpg.Pool, campaign_id: int, session_id: int, turn_id: int,
                       category: str, title: str, content: str,
                       reasoning: str | None = None) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO dm_notes
               (campaign_id, session_id, turn_id, category,
                title, content, reasoning)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING *""",
        campaign_id, session_id, turn_id, category, title, content, reasoning
    )
    return dict(row)


async def list_dm_notes(pool: asyncpg.Pool, campaign_id: int, category: str | None = None,
                        status: str = "active") -> list[dict]:
    """Browse layer: titles and metadata, no full content."""
    if category:
        rows = await pool.fetch(
            """SELECT id, category, title, status, turn_id,
                      session_id, created_at
               FROM dm_notes
               WHERE campaign_id = $1 AND category = $2 AND status = $3
               ORDER BY created_at DESC""",
            campaign_id, category, status
        )
    else:
        rows = await pool.fetch(
            """SELECT id, category, title, status, turn_id,
                      session_id, created_at
               FROM dm_notes
               WHERE campaign_id = $1 AND status = $2
               ORDER BY created_at DESC""",
            campaign_id, status
        )
    return [dict(r) for r in rows]


async def get_dm_note(pool: asyncpg.Pool, note_id: int) -> dict | None:
    """Full content retrieval for a specific note."""
    row = await pool.fetchrow(
        "SELECT * FROM dm_notes WHERE id = $1", note_id
    )
    return dict(row) if row else None


async def update_dm_note(pool: asyncpg.Pool, note_id: int, content: str | None = None,
                         reasoning: str | None = None,
                         status: str | None = None) -> dict | None:
    """Update specific fields on a note. Only non-None values change."""
    fields = []
    values = []
    idx = 1

    if content is not None:
        fields.append(f"content = ${idx}")
        values.append(content)
        idx += 1
    if reasoning is not None:
        fields.append(f"reasoning = ${idx}")
        values.append(reasoning)
        idx += 1
    if status is not None:
        fields.append(f"status = ${idx}")
        values.append(status)
        idx += 1

    if not fields:
        return await get_dm_note(pool, note_id)

    fields.append("updated_at = now()")
    values.append(note_id)

    query = f"""UPDATE dm_notes SET {', '.join(fields)}
                WHERE id = ${idx}
                RETURNING *"""

    row = await pool.fetchrow(query, *values)
    return dict(row) if row else None
