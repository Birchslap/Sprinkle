"""
Module: prompts.py
Dependencies: none

Loads the system prompt from file and appends campaign context.
"""

from pathlib import Path


_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Read a prompt file from the prompts directory."""
    path = _PROMPT_DIR / filename
    return path.read_text(encoding="utf-8")


def build_system_prompt(campaign_name: str = "", setting: str = "") -> str:
    """Build the full system prompt, optionally with campaign context."""
    parts = [_load_prompt("dm.md")]
    if campaign_name or setting:
        parts.append("\n\n# Current Campaign")
        if campaign_name:
            parts.append(f"\nCampaign: {campaign_name}")
        if setting:
            parts.append(f"\nSetting: {setting}")
    return "\n".join(parts)
