"""
load_rules.py — One-time loader for 5e.tools JSON data into rules_reference.

Usage:
    python load_rules.py [path_to_data_folder]

Defaults to ./resources/data if no path is given.
Connects to PostgreSQL using DATABASE_URL from .env or environment.
"""

from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Markup stripper
# ---------------------------------------------------------------------------
# 5e.tools uses {@tag content} and {@tag content|display} throughout.
# We want the human-readable part: the display text if present, else content.

TAG_RE = re.compile(r"\{@\w+\s+([^}|]+?)(?:\|([^}]+))?\}")


def strip_tags(text: str) -> str:
    """Strip 5e.tools {@tag} markup, keeping readable content."""
    def _replace(m):
        return m.group(2) if m.group(2) else m.group(1)
    return TAG_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Entry flatteners
# ---------------------------------------------------------------------------
# Each category has a slightly different shape but they all share the
# {@tag} markup and the "entries" array pattern.

def flatten_entries(entries: list, depth: int = 0) -> str:
    """Recursively flatten an entries array into readable text."""
    lines = []
    for entry in entries:
        if isinstance(entry, str):
            lines.append(strip_tags(entry))
        elif isinstance(entry, dict):
            if entry.get("type") == "list":
                for item in entry.get("items", []):
                    if isinstance(item, str):
                        lines.append(f"  • {strip_tags(item)}")
                    elif isinstance(item, dict):
                        lines.append(f"  • {strip_tags(item.get('name', ''))}: {flatten_entries(item.get('entries', []))}")
            elif entry.get("type") == "table":
                caption = entry.get("caption", "")
                if caption:
                    lines.append(f"Table: {strip_tags(caption)}")
                headers = entry.get("colLabels", [])
                if headers:
                    lines.append(" | ".join(strip_tags(h) for h in headers))
                for row in entry.get("rows", []):
                    if isinstance(row, list):
                        lines.append(" | ".join(strip_tags(str(cell)) for cell in row))
            elif "entries" in entry:
                name = entry.get("name", "")
                if name:
                    lines.append(f"\n{strip_tags(name)}:")
                lines.append(flatten_entries(entry["entries"], depth + 1))
            elif "name" in entry:
                lines.append(f"{strip_tags(entry['name'])}")
    return "\n".join(lines)


def flatten_monster(m: dict) -> str:
    """Flatten a bestiary entry into readable text."""
    lines = [f"# {m['name']}"]
    lines.append(f"Source: {m.get('source', 'Unknown')}")

    # Size, type, alignment
    size_map = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large",
                "H": "Huge", "G": "Gargantuan"}
    sizes = ", ".join(size_map.get(s, s) for s in m.get("size", []))
    mtype = m.get("type", "")
    if isinstance(mtype, dict):
        mtype = mtype.get("type", "")
    lines.append(f"{sizes} {mtype}, CR {m.get('cr', '?')}")

    # AC
    ac_parts = []
    for ac in m.get("ac", []):
        if isinstance(ac, int):
            ac_parts.append(str(ac))
        elif isinstance(ac, dict):
            s = str(ac.get("ac", ""))
            if ac.get("from"):
                s += f" ({', '.join(ac['from'])})"
            if ac.get("condition"):
                s += f" {strip_tags(ac['condition'])}"
            ac_parts.append(s)
    if ac_parts:
        lines.append(f"AC: {', '.join(ac_parts)}")

    # HP
    hp = m.get("hp", {})
    if isinstance(hp, dict):
        lines.append(f"HP: {hp.get('average', '?')} ({hp.get('formula', '')})")

    # Speed
    speed_parts = []
    for mode, val in m.get("speed", {}).items():
        if mode == "canHover":
            continue
        if isinstance(val, dict):
            speed_parts.append(f"{mode} {val.get('number', '')} ft.")
        else:
            speed_parts.append(f"{mode} {val} ft.")
    if speed_parts:
        lines.append(f"Speed: {', '.join(speed_parts)}")

    # Ability scores
    abilities = []
    for ab in ("str", "dex", "con", "int", "wis", "cha"):
        val = m.get(ab)
        if val is not None:
            abilities.append(f"{ab.upper()} {val}")
    if abilities:
        lines.append(" | ".join(abilities))

    # Saves
    saves = m.get("save", {})
    if saves:
        lines.append(f"Saves: {', '.join(f'{k.upper()} {v}' for k, v in saves.items())}")

    # Skills
    skills = m.get("skill", {})
    if skills:
        lines.append(f"Skills: {', '.join(f'{k.title()} {v}' for k, v in skills.items())}")

    # Senses, languages
    senses = m.get("senses", [])
    if senses:
        lines.append(f"Senses: {', '.join(senses)}, passive Perception {m.get('passive', '?')}")

    languages = m.get("languages", [])
    if languages:
        lines.append(f"Languages: {', '.join(languages)}")

    # Immunities, resistances
    def format_damage_list(lst):
        parts = []
        for item in lst:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                note = item.get("note", "")
                dmg = ", ".join(item.get("immune", item.get("resist", [])))
                parts.append(f"{dmg} ({note})" if note else dmg)
        return ", ".join(parts)

    immune = m.get("immune", [])
    if immune:
        lines.append(f"Damage Immunities: {format_damage_list(immune)}")

    resist = m.get("resist", [])
    if resist:
        lines.append(f"Damage Resistances: {format_damage_list(resist)}")

    cond_immune = m.get("conditionImmune", [])
    if cond_immune:
        lines.append(f"Condition Immunities: {', '.join(cond_immune)}")

    # Spellcasting
    for sc in m.get("spellcasting", []):
        lines.append(f"\nSpellcasting:")
        for header in sc.get("headerEntries", []):
            lines.append(strip_tags(header))
        for label, spells in [("At will", sc.get("will", [])),
                               ("1/day each", sc.get("daily", {}).get("1e", [])),
                               ("2/day each", sc.get("daily", {}).get("2e", [])),
                               ("3/day each", sc.get("daily", {}).get("3e", []))]:
            if spells:
                lines.append(f"  {label}: {', '.join(strip_tags(s) for s in spells)}")

    # Traits, actions, reactions, legendary actions
    for section_name, key in [("Traits", "trait"), ("Actions", "action"),
                               ("Reactions", "reaction"),
                               ("Legendary Actions", "legendary")]:
        items = m.get(key, [])
        if items:
            lines.append(f"\n{section_name}:")
            for item in items:
                name = strip_tags(item.get("name", ""))
                entries = flatten_entries(item.get("entries", []))
                lines.append(f"  {name}. {entries}")

    return "\n".join(lines)


def flatten_spell(s: dict) -> str:
    """Flatten a spell entry into readable text."""
    lines = [f"# {s['name']}"]
    lines.append(f"Source: {s.get('source', 'Unknown')}")
    lines.append(f"Level: {s.get('level', '?')}")

    school_map = {"A": "Abjuration", "C": "Conjuration", "D": "Divination",
                  "E": "Enchantment", "V": "Evocation", "I": "Illusion",
                  "N": "Necromancy", "T": "Transmutation"}
    lines.append(f"School: {school_map.get(s.get('school', ''), s.get('school', '?'))}")

    # Casting time
    for t in s.get("time", []):
        lines.append(f"Casting Time: {t.get('number', '')} {t.get('unit', '')}")

    # Range
    rng = s.get("range", {})
    rtype = rng.get("type", "")
    if rtype == "point":
        dist = rng.get("distance", {})
        lines.append(f"Range: {dist.get('amount', '')} {dist.get('type', '')}".strip())
    elif rtype == "special":
        lines.append("Range: Special")
    else:
        lines.append(f"Range: {rtype}")

    # Components
    comp = s.get("components", {})
    comp_parts = []
    if comp.get("v"):
        comp_parts.append("V")
    if comp.get("s"):
        comp_parts.append("S")
    if comp.get("m"):
        mat = comp["m"]
        if isinstance(mat, dict):
            comp_parts.append(f"M ({mat.get('text', '')})")
        else:
            comp_parts.append(f"M ({mat})")
    if comp_parts:
        lines.append(f"Components: {', '.join(comp_parts)}")

    # Duration
    for d in s.get("duration", []):
        dtype = d.get("type", "")
        if dtype == "timed":
            dur = d.get("duration", {})
            conc = "Concentration, " if d.get("concentration") else ""
            lines.append(f"Duration: {conc}{dur.get('amount', '')} {dur.get('type', '')}")
        elif dtype == "instant":
            lines.append("Duration: Instantaneous")
        elif dtype == "permanent":
            lines.append("Duration: Permanent")
        else:
            lines.append(f"Duration: {dtype}")

    # Classes
    classes = []
    for cls_entry in s.get("classes", {}).get("fromClassList", []):
        classes.append(cls_entry.get("name", ""))
    if classes:
        lines.append(f"Classes: {', '.join(classes)}")

    # Entries
    entries = s.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")

    higher = s.get("entriesHigherLevel", [])
    if higher:
        lines.append(f"\nAt Higher Levels: {flatten_entries(higher)}")

    return "\n".join(lines)


def flatten_race(r: dict) -> str:
    """Flatten a race entry into readable text."""
    lines = [f"# {r['name']}"]
    lines.append(f"Source: {r.get('source', 'Unknown')}")

    size_map = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large",
                "H": "Huge", "G": "Gargantuan"}
    sizes = r.get("size", [])
    if sizes:
        lines.append(f"Size: {', '.join(size_map.get(s, s) for s in sizes)}")

    speed = r.get("speed")
    if isinstance(speed, int):
        lines.append(f"Speed: {speed} ft.")
    elif isinstance(speed, dict):
        parts = []
        for mode, val in speed.items():
            if isinstance(val, int):
                parts.append(f"{mode} {val} ft.")
            elif isinstance(val, bool) and val:
                parts.append(mode)
        if parts:
            lines.append(f"Speed: {', '.join(parts)}")

    # Ability score increases
    ability = r.get("ability", [])
    for ab in ability:
        parts = [f"{k.upper()} +{v}" for k, v in ab.items() if k != "choose"]
        choose = ab.get("choose", {})
        if choose:
            parts.append(f"Choose {choose.get('count', 1)} from {', '.join(c.upper() for c in choose.get('from', []))}")
        if parts:
            lines.append(f"Ability Scores: {', '.join(parts)}")

    # Entries (racial traits)
    entries = r.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")

    return "\n".join(lines)


def flatten_class(c: dict) -> str:
    """Flatten a class entry into readable text."""
    lines = [f"# {c['name']}"]
    lines.append(f"Source: {c.get('source', 'Unknown')}")

    # Hit dice
    hd = c.get("hd", {})
    if hd:
        lines.append(f"Hit Die: d{hd.get('faces', '?')}")

    # Proficiencies
    profs = c.get("startingProficiencies", {})
    armor = profs.get("armor", [])
    if armor:
        lines.append(f"Armor: {', '.join(strip_tags(str(a)) for a in armor)}")
    weapons = profs.get("weapons", [])
    if weapons:
        lines.append(f"Weapons: {', '.join(strip_tags(str(w)) for w in weapons)}")
    skills = profs.get("skills", [])
    for sk in skills:
        if isinstance(sk, dict):
            choose = sk.get("choose", {})
            lines.append(f"Skills: Choose {choose.get('count', 2)} from {', '.join(strip_tags(s) for s in choose.get('from', []))}")

    # Class features
    features = c.get("classFeatures", [])
    if isinstance(features, list):
        for feat in features:
            if isinstance(feat, str):
                lines.append(f"\n{strip_tags(feat)}")
            elif isinstance(feat, dict):
                name = feat.get("name", feat.get("classFeature", ""))
                lines.append(f"\n{strip_tags(str(name))}")
                if "entries" in feat:
                    lines.append(flatten_entries(feat["entries"]))

    return "\n".join(lines)


def flatten_item(item: dict) -> str:
    """Flatten an item entry into readable text."""
    lines = [f"# {item['name']}"]
    lines.append(f"Source: {item.get('source', 'Unknown')}")

    itype = item.get("type", "")
    if itype:
        lines.append(f"Type: {itype}")

    rarity = item.get("rarity")
    if rarity and rarity != "none":
        lines.append(f"Rarity: {rarity}")

    weight = item.get("weight")
    if weight:
        lines.append(f"Weight: {weight} lb.")

    value = item.get("value")
    if value:
        # Value is in copper pieces
        if value >= 100:
            lines.append(f"Value: {value // 100} gp")
        else:
            lines.append(f"Value: {value} cp")

    # Weapon properties
    dmg1 = item.get("dmg1")
    dmgType = item.get("dmgType", "")
    if dmg1:
        lines.append(f"Damage: {dmg1} {dmgType}")

    props = item.get("property", [])
    if props:
        lines.append(f"Properties: {', '.join(props)}")

    # Attunement
    if item.get("reqAttune"):
        att = item["reqAttune"]
        if isinstance(att, str):
            lines.append(f"Requires Attunement: {strip_tags(att)}")
        else:
            lines.append("Requires Attunement")

    entries = item.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")

    return "\n".join(lines)


def flatten_feat(f: dict) -> str:
    """Flatten a feat entry into readable text."""
    lines = [f"# {f['name']}"]
    lines.append(f"Source: {f.get('source', 'Unknown')}")

    prereq = f.get("prerequisite", [])
    if prereq:
        prereq_parts = []
        for p in prereq:
            if "ability" in p:
                for ab in p["ability"]:
                    prereq_parts.extend(f"{k.upper()} {v}+" for k, v in ab.items())
            if "race" in p:
                prereq_parts.extend(r.get("name", "") for r in p["race"])
            if "level" in p:
                prereq_parts.append(f"Level {p['level']}")
        if prereq_parts:
            lines.append(f"Prerequisite: {', '.join(prereq_parts)}")

    entries = f.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")

    return "\n".join(lines)


def flatten_background(b: dict) -> str:
    """Flatten a background entry into readable text."""
    lines = [f"# {b['name']}"]
    lines.append(f"Source: {b.get('source', 'Unknown')}")

    entries = b.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")

    return "\n".join(lines)


def flatten_condition(c: dict) -> str:
    """Flatten a condition entry into readable text."""
    lines = [f"# {c['name']}"]
    lines.append(f"Source: {c.get('source', 'Unknown')}")

    entries = c.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")

    return "\n".join(lines)


def flatten_generic(entry: dict) -> str:
    """Generic fallback flattener for any entry with name + entries."""
    lines = [f"# {entry.get('name', 'Unknown')}"]
    source = entry.get("source", "")
    if source:
        lines.append(f"Source: {source}")
    entries = entry.get("entries", [])
    if entries:
        lines.append(f"\n{flatten_entries(entries)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File processors
# ---------------------------------------------------------------------------
# Maps JSON keys to (category_name, flattener_function)

PROCESSORS = {
    "monster":      ("monster",     flatten_monster),
    "spell":        ("spell",       flatten_spell),
    "race":         ("race",        flatten_race),
    "subrace":      ("race",        flatten_race),
    "class":        ("class",       flatten_class),
    "item":         ("item",        flatten_item),
    "baseitem":     ("item",        flatten_item),
    "magicvariant": ("item",        flatten_item),
    "feat":         ("feat",        flatten_feat),
    "background":   ("background",  flatten_background),
    "condition":    ("condition",    flatten_condition),
    "disease":      ("condition",    flatten_condition),
    "action":       ("action",      flatten_generic),
    "optionalfeature": ("optional_feature", flatten_generic),
    "reward":       ("reward",      flatten_generic),
    "trap":         ("trap",        flatten_generic),
    "hazard":       ("hazard",      flatten_generic),
    "object":       ("object",      flatten_generic),
    "vehicle":      ("vehicle",     flatten_generic),
    "legendaryGroup": ("legendary_group", flatten_generic),
}


def process_file(filepath: Path) -> list[tuple[str, str, str, str]]:
    """Process a single JSON file. Returns list of (category, name, source, content)."""
    rows = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"  Skipping {filepath.name}: {e}")
        return rows

    if not isinstance(data, dict):
        return []

    for key, (category, flattener) in PROCESSORS.items():
        entries = data.get(key, [])
        if not entries or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or "name" not in entry:
                continue
            # Skip "_copy" entries (references to other entries, not real content)
            if "_copy" in entry and "entries" not in entry:
                continue
            try:
                name = entry["name"]
                source = entry.get("source", "Unknown")
                content = flattener(entry)
                rows.append((category, name, source, content))
            except Exception as e:
                print(f"  Error processing {entry.get('name', '?')} in {filepath.name}: {e}")

    return rows


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

async def load(data_path: str):
    """Walk the data directory, process all JSON files, load into PostgreSQL."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

    data_dir = Path(data_path)
    if not data_dir.exists():
        print(f"ERROR: Data directory not found: {data_dir}")
        sys.exit(1)

    # Gather all JSON files
    json_files = sorted(data_dir.rglob("*.json"))
    print(f"Found {len(json_files)} JSON files in {data_dir}")

    # Process all files
    all_rows = []
    for filepath in json_files:
        rows = process_file(filepath)
        if rows:
            print(f"  {filepath.relative_to(data_dir)}: {len(rows)} entries")
            all_rows.extend(rows)

    print(f"\nTotal entries: {len(all_rows)}")

    if not all_rows:
        print("Nothing to load.")
        return

    # Load into PostgreSQL
    conn = await asyncpg.connect(db_url, ssl=False)
    try:
        # Clear existing data
        await conn.execute("DELETE FROM rules_reference")
        print("Cleared existing rules_reference data.")

        # Batch insert
        await conn.executemany(
            "INSERT INTO rules_reference (category, name, source, content) VALUES ($1, $2, $3, $4)",
            all_rows
        )
        print(f"Loaded {len(all_rows)} entries into rules_reference.")

        # Report categories
        cats = await conn.fetch(
            "SELECT category, COUNT(*) as cnt FROM rules_reference GROUP BY category ORDER BY cnt DESC"
        )
        print("\nBreakdown by category:")
        for row in cats:
            print(f"  {row['category']:20s} {row['cnt']:>6,}")

    finally:
        await conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "./resources/data"
    asyncio.run(load(path))
