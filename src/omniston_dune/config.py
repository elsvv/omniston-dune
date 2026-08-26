from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# 2026-03-25T00:00:00Z, a week of margin before history begins in April 2026.
# Starting earlier costs one wasted request per cube and returns nothing.
DEFAULT_HISTORY_START_TS = 1774396800

USER_AGENT = "omniston-dune/0.1 (+https://docs.ston.fi/developer-section/omniston/history)"


class ConfigError(RuntimeError):
    """Raised when a required setting is absent."""


@dataclass(frozen=True)
class Settings:
    dune_api_key: str
    dune_namespace: str
    user_agent: str
    history_start_ts: int


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        dune_api_key=_require(env, "DUNE_API_KEY"),
        dune_namespace=_require(env, "DUNE_NAMESPACE"),
        user_agent=env.get("OMNISTON_USER_AGENT", USER_AGENT),
        history_start_ts=int(env.get("HISTORY_START_TS", DEFAULT_HISTORY_START_TS)),
    )
