"""
Load DM protocols from prompts/ directory into the dm_protocols table.
Reads each .md file (except system_prompt.md), uses the filename as the
protocol name, extracts the title from the first markdown heading, and
upserts into the database.

Usage:
    python load_protocols.py
"""

import asyncio
import os
from pathlib import Path

import asyncpg

# ── Read .env ────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


PROMPTS_DIR = Path(__file__).parent / "prompts"
SKIP_FILES = {"system_prompt.md"}


async def load():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set.")
        return

    # Collect protocol files
    files = sorted(
        f for f in PROMPTS_DIR.glob("*.md")
        if f.name not in SKIP_FILES
    )
    if not files:
        print(f"No protocol files found in {PROMPTS_DIR}")
        return

    print(f"Found {len(files)} protocol file(s):")
    for f in files:
        print(f"  {f.name}")

    conn = await asyncpg.connect(db_url, ssl=False)
    try:
        for f in files:
            name = f.stem  # e.g. "npc_generation"
            content = f.read_text(encoding="utf-8")

            # Extract title from first heading
            title = name.replace("_", " ").title()
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line.lstrip("# ").strip()
                    break

            await conn.execute(
                """
                INSERT INTO dm_protocols (name, title, content, updated_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (name) DO UPDATE
                SET title = $2, content = $3, updated_at = now()
                """,
                name, title, content,
            )
            print(f"  Loaded: {name} ({title})")

        print(f"\nDone. {len(files)} protocol(s) loaded.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(load())
