from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

# 2026-03-25T00:00:00Z, a week of margin before history begins in April 2026.
# Starting earlier costs one wasted request per cube and returns nothing.
DEFAULT_HISTORY_START_TS = 1774396800

USER_AGENT = "omniston-dune/0.1 (+https://docs.ston.fi/developer-section/omniston/history)"


class ConfigError(RuntimeError):
    """Raised when a required setting is absent or unusable."""


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


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read an integer override, naming the variable and value when it is bad.

    A bare int() here raises ValueError, which __main__ does not catch -- it
    catches this project's own error types and lets anything else through as a
    bug with its traceback. A typo in an environment variable is not a bug, so
    it gets an operator-readable message like every other config failure.
    """
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name} must be a whole number of seconds "
            f"since the Unix epoch, but is {raw!r}. Remove it to use the "
            f"default of {default}."
        ) from exc


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        # Load .env into the real process environment, but never override a
        # variable that is already set there. override=False is
        # python-dotenv's default, but pass it explicitly -- a test asserts
        # this behaviour rather than trusting the default, because getting it
        # backwards would mean a stale local .env silently overrides real
        # values. Locally, .env supplies DUNE_API_KEY etc.; in CI, they arrive
        # as real environment variables from repository secrets, and a stale
        # .env must never win against those.
        # find_dotenv() searches upward from this file's location -- the
        # project root -- not the current working directory, so this behaves
        # the same no matter where the tool is invoked from. It returns ""
        # when no .env exists anywhere in that walk, and load_dotenv("") is a
        # safe no-op, so a missing file is not an error. Only do this when no
        # explicit `env` mapping was passed in: that path (used by tests) must
        # not be contaminated by whatever .env happens to be on disk.
        load_dotenv(find_dotenv(), override=False)
    env = os.environ if env is None else env
    return Settings(
        dune_api_key=_require(env, "DUNE_API_KEY"),
        dune_namespace=_require(env, "DUNE_NAMESPACE"),
        user_agent=env.get("OMNISTON_USER_AGENT", USER_AGENT),
        history_start_ts=_int(env, "HISTORY_START_TS", DEFAULT_HISTORY_START_TS),
    )
