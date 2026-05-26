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


def build_system_prompt(campaign_name: str = "", setting: str = "",
                        character_doc: str | None = None) -> str:
    """Build the full system prompt, optionally with campaign context."""
    parts = [_load_prompt("system_prompt.md"), _load_prompt("technical_instructions.md")]
    if campaign_name or setting or character_doc:
        parts.append("\n\n# Current Campaign")
        if campaign_name:
            parts.append(f"\nCampaign: {campaign_name}")
        if setting:
            parts.append(f"\nSetting: {setting}")
        if character_doc:
            parts.append(f"\n\n## Player Character\n\n{character_doc}")
    return "\n".join(parts)
