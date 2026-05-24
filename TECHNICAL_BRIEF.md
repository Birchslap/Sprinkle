# Sprinkle — Technical Architecture Brief

**Purpose:** This document tells Calliope everything the DM model needs to know about its own infrastructure. Use it as a reference when writing technical instructions in the system prompt. The model needs to understand its tools, its memory, and how messages flow — so it can use them well and avoid confusing the player.

---

## 1. What the Model Is

The model is a Dungeon Master running a persistent, single-player campaign. It runs on xAI's API (Grok), streaming responses in real time via WebSocket. Every message — player input, DM responses, tool calls, tool results — is saved to a PostgreSQL database and persists across sessions.

The player can close their browser and come back days later. The campaign continues. The model should behave as if it remembers everything — because it does, within the limits of its context window.

---

## 2. Tools

The model has 13 tools available. These are its hands — how it interacts with the persistent world beyond generating text. Tool calls are **invisible to the player**. The player never sees tool names, arguments, or raw results. They only see the narrative the model writes after using them.

### Dice
- **`roll_dice`** — Roll any standard dice expression (e.g., `1d20`, `2d6+3`, `4d6-1`). Returns individual rolls, modifier, and total. The model should always narrate the result — "You swing your sword..." then describe the outcome based on the roll.

### Characters
- **`save_character`** — Create or update a character (PC or NPC). Fields: name, type (pc/npc), description, stats (freeform JSON), and private DM notes. If a character with the same name exists, it updates rather than duplicates.
- **`get_character`** — Retrieve full details of a named character.
- **`list_characters`** — List all characters in the campaign. Can filter by type.

### Locations
- **`save_location`** — Create or update a named location. Fields: name, description, and private DM notes.
- **`get_location`** — Retrieve full details of a named location.
- **`list_locations`** — List all locations in the campaign.

### Events
- **`save_event`** — Record a significant narrative event. Fields: summary, details, significance. Events are the campaign's historical record. The model should save an event whenever something happens that would matter later — a major battle, a betrayal, arriving at a new city, making an important alliance.

### DM Notes (Private Memory)
This is the model's most important tool system. DM notes are **the model's private memory** — the player never sees them. They are how the model plans, tracks secrets, records motivations, and maintains narrative coherence across sessions.

- **`save_dm_note`** — Save a private note. Fields: category, title, content, and reasoning (why the model is making this note). Categories are freeform — suggested types include: `plot_plan`, `intention`, `secret`, `foreshadowing`, `observation`, `npc_motivation`.
- **`get_dm_note`** — Retrieve a specific note by ID.
- **`list_dm_notes`** — Browse notes. Can filter by category and status (active/resolved/abandoned).
- **`update_dm_note`** — Update an existing note's content, reasoning, or status. Use this to mark plans as resolved, update evolving secrets, or abandon dead threads.

### Context
- **`get_turn_context`** — Retrieve all messages from a specific turn number. Useful for reviewing what was happening when a note or event was created.

### Tool Usage Guidance for the System Prompt

The model should be instructed to:

1. **Use DM notes aggressively.** Every secret, every plan, every NPC motivation, every foreshadowing thread should be noted. When the model has a clever idea for where the story could go — note it. When it notices the player seems interested in something — note it. The notes system is what makes the DM *actually* intelligent across sessions rather than just reactive.

2. **Save characters and locations as they're introduced.** Don't wait. The moment an NPC speaks or a place is described, save it. This builds the persistent world.

3. **Save events at narrative inflection points.** Not every turn — but every moment that changes the shape of the story.

4. **Review notes and characters before major scenes.** A quick `list_dm_notes` or `get_character` before a reunion, a confrontation, or a plot reveal ensures the model is working from its own established continuity rather than improvising blind.

5. **Update note status.** When a plot thread resolves, mark it resolved. When a plan is abandoned, mark it abandoned. This keeps the notes system useful rather than cluttered.

6. **Use the reasoning field.** When saving a DM note, the reasoning field captures *why* the model is making this choice. This is invaluable when the model retrieves the note later and needs to remember its own thinking.

---

## 3. Context Window — Increment and Chop

The model does not see the entire campaign history. It sees a window of recent messages, managed by an increment-and-chop strategy:

- Messages accumulate in the context window up to a ceiling of **150 messages**.
- When the ceiling is hit, the window is chopped down to the most recent **50 messages**.
- The window then grows again from 50 toward 150.

**What this means for the model:**
- Early messages will eventually leave the context window. This is why DM notes, saved characters, saved locations, and saved events matter — they persist in the database even when the messages that created them have scrolled out of context.
- The model should **not rely on remembering** conversations from many turns ago. It should rely on its tools. If it noted something important, it can retrieve it. If it saved a character, it can look them up.
- The system prompt should instruct the model that **when resuming**, it should open with a brief narrative recap of where things left off. The player will have their chat history visible, but a short "When we last left off..." from the DM makes the return feel intentional.

---

## 4. Message Flow

When the player sends a message, here's what happens:

1. Player message is saved to the database.
2. The system builds the message list: system prompt + context window (oldest first) + current player message.
3. The model streams its response.
4. If the model calls tools: each tool is dispatched, results are saved, and the model is re-prompted with the results. This can happen up to **15 times** in a single turn (the safety limit for tool round-trips).
5. Once the model produces a pure text response (no tool calls), it's saved and the turn is complete.

**Important:** The model can call multiple tools in a single response, and it can alternate between tool calls and narrative text across multiple rounds within a single turn. A typical complex turn might look like:

- Round 1: Model calls `roll_dice("1d20")` and `get_character("Vex")`
- Round 2: Model receives results, calls `save_event(...)` to record the outcome
- Round 3: Model writes the narrative response the player sees

All of this happens within one player turn. The player sees only the final narrative.

---

## 5. What the Player Sees vs. What the Model Sees

This distinction matters for how the system prompt instructs the model to behave.

**The player sees:**
- Their own messages
- The DM's narrative responses
- Nothing else

**The player does NOT see:**
- Tool calls or tool results
- DM notes
- The system prompt
- Raw dice roll data (they see the narrated outcome)

**The model sees:**
- The system prompt (always first)
- The context window of messages (including tool calls and tool results from previous turns)
- The current player message

This means the model must always narrate tool results into its response. A bare tool call with no follow-up text would leave the player staring at silence.

---

## 6. Character Document

When a player creates a new campaign, they may provide a character document — anything from a full stat block to a narrative description of their character. If provided, this text is embedded in the system prompt under a `## Player Character` heading.

The model should:
- Parse whatever the player provided and work with it.
- Use `save_character` to persist the PC's details on the first turn.
- Acknowledge who the player is in its opening narration.
- If no character document is provided, open by asking the player about their character.

---

## 7. Session Management

Campaigns persist across sessions. A player might play for an hour, close the browser, and return three days later. When they reconnect:

- A new session is created.
- The message history is sent to the frontend so the player can see previous conversation.
- The model's context window is rebuilt from the database.

The model doesn't need to explicitly manage sessions — the infrastructure handles it. But the system prompt should instruct the model that **when resuming**, it should open with a brief narrative recap of where things left off. The player will have their chat history visible, but a short "When we last left off..." from the DM makes the return feel intentional.

---

## 8. Streaming

Responses are streamed token-by-token to the player via WebSocket. The frontend renders them with a typewriter effect. This means:

- The model should write in a way that reads well progressively — not front-loading all the important information at the end of a long paragraph.
- Opening with something engaging matters more than usual, because the player is watching the text appear in real time.
- Paragraph breaks and natural pacing help the reading experience during streaming.

---

## 9. Summary of Technical Instructions for the System Prompt

These are the key technical behaviours the system prompt needs to instruct:

1. **You have tools. Use them.** Especially DM notes — they are your memory.
2. **Save early, save often.** Characters, locations, events, and notes should be created the moment they become relevant.
3. **Your context window is finite.** Don't rely on remembering old conversations. Rely on your saved data.
4. **Tool calls are invisible.** Always narrate results. Never leave the player without a response.
5. **You can make multiple tool calls per turn.** Use them in sequence when needed — check notes before a scene, roll dice during it, save the outcome after.
6. **When resuming a campaign, recap briefly.** The player can see their history, but a narrative "welcome back" sets the tone.
7. **The reasoning field on DM notes is for you.** Use it to record your thinking. Future-you will thank present-you.
8. **Up to 15 tool rounds per turn.** You won't hit this in normal play, but you have room for complex sequences.
9. **Stream-friendly writing.** Open strong, pace naturally, use paragraph breaks.
