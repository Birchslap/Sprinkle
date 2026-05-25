"""
Module: models.py
Dependencies: none

Shared data shapes used across multiple modules.
Renamed from types.py to avoid shadowing Python's built-in types module.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContentDelta:
    """A fragment of streamed text from the model."""
    text: str


@dataclass(frozen=True)
class ToolCallRequest:
    """A complete tool call parsed from the model's response."""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageData:
    """Token usage from a single API call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
