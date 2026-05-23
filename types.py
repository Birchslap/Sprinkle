"""
Module: types.py
Dependencies: none

Shared data shapes used across multiple modules.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContentDelta:
    """A fragment of streamed text from the model."""
    text: str


@dataclass(frozen=True)
class ToolCallRequest:
    """A complete tool call parsed from the model's response."""
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
