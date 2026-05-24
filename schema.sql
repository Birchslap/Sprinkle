-- ============================================================
-- Sprinkle Schema
-- ============================================================

-- Campaigns
CREATE TABLE campaigns (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    setting         TEXT,
    character_doc   TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sessions within a campaign
CREATE TABLE sessions (
    id          SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    summary     TEXT
);

-- Complete message transcript
CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id     INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tool_name   TEXT,
    tool_data   JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Characters (PCs and NPCs)
CREATE TABLE characters (
    id              SERIAL PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    character_type  TEXT NOT NULL,
    description     TEXT,
    stats           JSONB,
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Locations
CREATE TABLE locations (
    id          SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    notes       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Events (things that happened in the narrative)
CREATE TABLE events (
    id           SERIAL PRIMARY KEY,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    session_id   INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id      INTEGER,
    summary      TEXT NOT NULL,
    details      TEXT,
    significance TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- DM Notes (the director's notebook)
CREATE TABLE dm_notes (
    id          SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id     INTEGER,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    reasoning   TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Token usage per API call
CREATE TABLE token_usage (
    id                  SERIAL PRIMARY KEY,
    session_id          INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id             INTEGER NOT NULL,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    cached_tokens       INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rules reference (5e content, loaded from 5e.tools data)
CREATE TABLE rules_reference (
    id          SERIAL PRIMARY KEY,
    category    TEXT NOT NULL,
    name        TEXT NOT NULL,
    source      TEXT,
    content     TEXT NOT NULL,
    search_vec  tsvector GENERATED ALWAYS AS (to_tsvector('english', name || ' ' || content)) STORED
);

-- Indexes
CREATE INDEX idx_messages_session    ON messages(session_id, created_at);
CREATE INDEX idx_messages_turn       ON messages(session_id, turn_id);
ALTER TABLE characters ADD CONSTRAINT uq_characters_campaign_name UNIQUE (campaign_id, name);
CREATE INDEX idx_characters_campaign ON characters(campaign_id, status);
ALTER TABLE locations ADD CONSTRAINT uq_locations_campaign_name UNIQUE (campaign_id, name);
CREATE INDEX idx_locations_campaign  ON locations(campaign_id, status);
CREATE INDEX idx_events_campaign     ON events(campaign_id, created_at);
CREATE INDEX idx_dm_notes_campaign   ON dm_notes(campaign_id, category, status);
CREATE INDEX idx_dm_notes_turn       ON dm_notes(session_id, turn_id);
CREATE INDEX idx_token_usage_session ON token_usage(session_id, turn_id);
CREATE INDEX idx_sessions_campaign   ON sessions(campaign_id);
CREATE INDEX idx_rules_search        ON rules_reference USING GIN(search_vec);
CREATE INDEX idx_rules_category      ON rules_reference(category, name);
