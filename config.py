"""
Module: config.py
Dependencies: none

Loads environment variables and exposes application configuration.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "xai"
    model: str = "grok-4.3"
    api_key: str = ""
    base_url: str = "https://api.x.ai/v1"
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass(frozen=True)
class DatabaseConfig:
    url: str = ""


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    database: DatabaseConfig
    host: str = "127.0.0.1"
    port: int = 8000
    history_limit: int = 100


def load_config() -> AppConfig:
    """Build configuration from environment variables."""
    return AppConfig(
        model=ModelConfig(
            api_key=os.environ.get("XAI_API_KEY", ""),
            model=os.environ.get("SPRINKLE_MODEL", "grok-4.3"),
            temperature=float(os.environ.get("SPRINKLE_TEMPERATURE", "0.7")),
            max_tokens=int(os.environ.get("SPRINKLE_MAX_TOKENS", "4096")),
        ),
        database=DatabaseConfig(
            url=os.environ.get("DATABASE_URL", ""),
        ),
        host=os.environ.get("SPRINKLE_HOST", "127.0.0.1"),
        port=int(os.environ.get("SPRINKLE_PORT", "8000")),
        history_limit=int(os.environ.get("SPRINKLE_HISTORY_LIMIT", "100")),
    )
