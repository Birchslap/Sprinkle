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

async def create_campaign(pool: asyncpg.Pool, name: str, setting: str | None = None,
                         character_doc: str | None = None) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO campaigns (name, setting, character_doc)
           VALUES ($1, $2, $3)
           RETURNING *""",
        name, setting, character_doc
    )
    return dict(row)


async def get_campaign(pool: asyncpg.Pool, campaign_id: int) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM campaigns WHERE id = $1", campaign_id
    )
    return dict(row) if row else None


async def list_campaigns(pool: asyncpg.Pool) -> list[dict]:
    """All campaigns, newest first."""
    rows = await pool.fetch(
        "SELECT * FROM campaigns ORDER BY updated_at DESC"
    )
    results = []
    for r in rows:
        d = dict(r)
        if d.get("tool_data") and isinstance(d["tool_data"], str):
            d["tool_data"] = json.loads(d["tool_data"])
        results.append(d)
    return results


async def delete_campaign(pool: asyncpg.Pool, campaign_id: int) -> bool:
    """Delete a campaign and all associated data. CASCADE handles child rows."""
    result = await pool.execute(
        "DELETE FROM campaigns WHERE id = $1", campaign_id
    )
    return result == "DELETE 1"


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


async def get_message_history(pool: asyncpg.Pool, campaign_id: int,
                              limit: int = 100) -> list[dict]:
    """Player-visible chat history for a campaign.

    Returns user and assistant messages only (no tool calls, tool results,
    or system messages). This is the rendered narrative view, not the full
    transcript the model receives via _build_messages.
    """
    rows = await pool.fetch(
        """SELECT m.role, m.content, m.turn_id, m.created_at
           FROM messages m
           JOIN sessions s ON m.session_id = s.id
           WHERE s.campaign_id = $1
             AND m.role IN ('user', 'assistant')
             AND m.tool_name IS NULL
             AND m.tool_data IS NULL
           ORDER BY m.created_at DESC
           LIMIT $2""",
        campaign_id, limit
    )
    # Return in chronological order (query is newest-first for LIMIT).
    return [dict(r) for r in reversed(rows)]


async def get_campaign_messages(
    pool: asyncpg.Pool,
    campaign_id: int,
    limit: int = 150,
) -> list[dict]:
    """Full message history across all sessions for a campaign.

    Returns ALL message types including tool calls and results — this is
    the model's context, not the player-visible view. Newest first; caller
    reverses to chronological order after any slicing.
    """
    rows = await pool.fetch(
        """SELECT m.role, m.content, m.tool_name, m.tool_data
           FROM messages m
           JOIN sessions s ON m.session_id = s.id
           WHERE s.campaign_id = $1
           ORDER BY m.created_at DESC
           LIMIT $2""",
        campaign_id, limit
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


async def get_last_turn_id(pool: asyncpg.Pool, campaign_id: int) -> int:
    """Recover the most recent turn_id across all sessions for a campaign."""
    row = await pool.fetchrow(
        """SELECT turn_id FROM messages
           WHERE session_id IN (
               SELECT id FROM sessions WHERE campaign_id = $1
           )
           ORDER BY created_at DESC LIMIT 1""",
        campaign_id
    )
    return row["turn_id"] if row else 0


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


# -- Token Usage --------------------------------------------------------------

async def save_token_usage(
    pool: asyncpg.Pool,
    session_id: int,
    turn_id: int,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    total_tokens: int,
) -> dict:
    """Record token usage from a single API call."""
    row = await pool.fetchrow(
        """INSERT INTO token_usage
               (session_id, turn_id, prompt_tokens, completion_tokens,
                cached_tokens, total_tokens)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *""",
        session_id, turn_id, prompt_tokens, completion_tokens,
        cached_tokens, total_tokens
    )
    return dict(row)


async def get_chat_export(
    pool: asyncpg.Pool,
    campaign_id: int,
) -> list[dict]:
    """Full player-visible message history for export.

    Returns all user and assistant messages in chronological order
    with no limit. Used for markdown chat transcript downloads.
    """
    rows = await pool.fetch(
        """SELECT m.role, m.content, m.turn_id, m.created_at
           FROM messages m
           JOIN sessions s ON m.session_id = s.id
           WHERE s.campaign_id = $1
             AND m.role IN ('user', 'assistant')
             AND m.tool_name IS NULL
             AND m.tool_data IS NULL
           ORDER BY m.created_at""",
        campaign_id
    )
    return [dict(r) for r in rows]


async def get_campaign_usage_detail(
    pool: asyncpg.Pool,
    campaign_id: int,
) -> list[dict]:
    """Per-call token usage rows for CSV export.

    Returns one row per API call in chronological order, with session_id,
    turn_id, all token counts, cache hit rate, and timestamp.
    """
    rows = await pool.fetch(
        """SELECT
               t.session_id,
               t.turn_id,
               t.prompt_tokens,
               t.completion_tokens,
               t.cached_tokens,
               t.total_tokens,
               CASE WHEN t.prompt_tokens > 0
                    THEN ROUND(t.cached_tokens::numeric / t.prompt_tokens * 100, 1)
                    ELSE 0
               END AS cache_hit_pct,
               t.created_at
           FROM token_usage t
           JOIN sessions s ON t.session_id = s.id
           WHERE s.campaign_id = $1
           ORDER BY t.created_at""",
        campaign_id
    )
    return [dict(r) for r in rows]


async def search_rules(
    pool: asyncpg.Pool,
    query: str,
    category: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search the rules reference using full-text search.

    Returns matching entries ranked by relevance. Optionally filter
    by category (e.g., 'monster', 'spell', 'race', 'class').
    """
    tsquery = " & ".join(query.strip().split())
    if category:
        rows = await pool.fetch(
            """SELECT name, category, source, content,
                      ts_rank(search_vector, to_tsquery('english', $1)) AS rank
               FROM rules_reference
               WHERE search_vector @@ to_tsquery('english', $1)
                 AND category = $2
               ORDER BY rank DESC
               LIMIT $3""",
            tsquery, category, limit
        )
    else:
        rows = await pool.fetch(
            """SELECT name, category, source, content,
                      ts_rank(search_vector, to_tsquery('english', $1)) AS rank
               FROM rules_reference
               WHERE search_vector @@ to_tsquery('english', $1)
               ORDER BY rank DESC
               LIMIT $2""",
            tsquery, limit
        )
    return [dict(r) for r in rows]


async def get_protocol(
    pool: asyncpg.Pool,
    name: str,
) -> dict | None:
    """Retrieve a DM protocol by name."""
    row = await pool.fetchrow(
        "SELECT name, title, content FROM dm_protocols WHERE name = $1",
        name,
    )
    if not row:
        return None
    return dict(row)


async def get_campaign_usage(
    pool: asyncpg.Pool,
    campaign_id: int,
) -> dict:
    """Aggregate token usage across all sessions for a campaign.

    Returns totals for prompt, completion, cached, and total tokens,
    plus the number of API calls made.
    """
    row = await pool.fetchrow(
        """SELECT
               COALESCE(SUM(t.prompt_tokens), 0)     AS prompt_tokens,
               COALESCE(SUM(t.completion_tokens), 0)  AS completion_tokens,
               COALESCE(SUM(t.cached_tokens), 0)      AS cached_tokens,
               COALESCE(SUM(t.total_tokens), 0)        AS total_tokens,
               COUNT(*)                                AS api_calls
           FROM token_usage t
           JOIN sessions s ON t.session_id = s.id
           WHERE s.campaign_id = $1""",
        campaign_id
    )
    return dict(row)
