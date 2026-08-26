from __future__ import annotations

import json
import time
from collections.abc import Sequence

import requests

BASE_URL = "https://api.dune.com/api/v1"

# /insert accepts up to 1.2GB per request. This project's largest table is a few
# megabytes, so chunking is a guard rail rather than a necessity.
CHUNK_SIZE = 50_000

# Dune's free tier allows 15 requests per minute on write endpoints. A run
# issues one create + one clear + one insert per table (7 tables = 21 write
# requests) plus one execute per dashboard query -- call it 39 requests for a
# typical run. Spacing every write 6.0s apart yields a sustained rate of
# 60 / 6.0 = 10 requests/minute, a 33% margin under the 15/minute limit. The
# previous value of 4.5s produced 60 / 4.5 = 13.3 requests/minute, only an 11%
# margin -- close enough to the ceiling that 429s were likely. The extra
# minute or so of wall-clock this adds to a run buys a large reduction in 429
# risk, and that is the right trade for an unattended nightly job: a 429 that
# survives the retries can cost up to a minute of waiting, and in the worst
# case that pushes the run into the workflow timeout mid-publish -- the exact
# partial-refresh corruption this throttle exists to prevent.
MIN_WRITE_INTERVAL_SECONDS = 6.0

# A 429 that slips past the throttle is retried this many times before the
# caller sees a DuneError.
MAX_RATE_LIMIT_RETRIES = 2

# Used when a 429 carries no Retry-After header: the limit is per minute, so a
# full minute is the shortest wait guaranteed to clear the window.
DEFAULT_RETRY_AFTER_SECONDS = 60.0

# A Retry-After longer than this is treated as malformed or hostile: waiting
# it out would exceed any sane job budget, so the wait is clamped and the
# request simply retries sooner, failing fast with a DuneError if the limit
# really is still in force.
MAX_RETRY_AFTER_SECONDS = 300.0

# monotonic timestamp of the last outbound write request, or None before the
# first one. Wall clock is not used: it can jump backwards (NTP, DST) and turn
# the throttle into either a no-op or a very long sleep.
_last_write_monotonic: float | None = None


class DuneError(RuntimeError):
    """A non-success response from the Dune API."""


def _headers(api_key: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"X-DUNE-API-KEY": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _fail(action: str, response: requests.Response) -> DuneError:
    return DuneError(f"{action} returned HTTP {response.status_code}: {response.text[:300]}")


def _throttle() -> None:
    """Sleep until MIN_WRITE_INTERVAL_SECONDS has passed since the last write.

    The interval is read from the module attribute on every call rather than
    captured at import, so tests can set it to 0 and run instantly.
    """
    global _last_write_monotonic

    interval = MIN_WRITE_INTERVAL_SECONDS
    now = time.monotonic()
    if _last_write_monotonic is not None:
        wait = interval - (now - _last_write_monotonic)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
    _last_write_monotonic = now


def _retry_after_seconds(response: requests.Response) -> float:
    """Seconds to wait after a 429, honouring Retry-After when it is usable."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(str(raw).strip())))
    except ValueError:
        # Retry-After may also be an HTTP-date. Rather than guess at a format,
        # fall back to the full window.
        return DEFAULT_RETRY_AFTER_SECONDS


def _post(url: str, **kwargs) -> requests.Response:
    """Throttled POST that retries a rate-limited request.

    Every outbound write goes through here so the 15/minute pacing and the 429
    handling apply uniformly. The response is returned rather than checked:
    each endpoint decides for itself which statuses are acceptable (create_table
    treats an existing table as success), and a 429 that survives the retries
    reaches that same error path.
    """
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        _throttle()
        response = requests.post(url, **kwargs)
        if response.status_code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
            return response
        time.sleep(_retry_after_seconds(response))
    raise AssertionError("unreachable")  # pragma: no cover


def create_table(
    api_key: str,
    namespace: str,
    table_name: str,
    schema: Sequence[dict],
    *,
    description: str = "",
    is_private: bool = False,
) -> dict:
    """Create the table, treating an already-existing table as success.

    Creation costs credits, so this is called once per table per run and the
    conflict path is the normal path after the first run. The substring check
    is restricted to client-error statuses so a 5xx that happens to leak
    "already exists" in its body (e.g. a database error) is never mistaken
    for a pre-existing table.
    """
    response = _post(
        f"{BASE_URL}/uploads",
        json={
            "namespace": namespace,
            "table_name": table_name,
            "description": description,
            "is_private": is_private,
            "schema": list(schema),
        },
        headers=_headers(api_key, "application/json"),
        timeout=60,
    )
    if response.status_code == 200:
        return response.json()
    if response.status_code in (400, 409) and "already exists" in response.text.lower():
        return {"already_exists": True}
    raise _fail(f"create_table {namespace}.{table_name}", response)


def clear_table(api_key: str, namespace: str, table_name: str) -> None:
    """Empty a table. Dune cannot delete a date range, only the whole table."""
    response = _post(
        f"{BASE_URL}/uploads/{namespace}/{table_name}/clear",
        headers=_headers(api_key),
        timeout=120,
    )
    if response.status_code != 200:
        raise _fail(f"clear_table {namespace}.{table_name}", response)


def insert_rows(
    api_key: str,
    namespace: str,
    table_name: str,
    rows: Sequence[dict],
    *,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """Append rows as NDJSON.

    Each request is all-or-nothing: HTTP 200 means every row in that request
    landed, any other status means none of them did.
    """
    if not rows:
        return 0

    sent = 0
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        body = "\n".join(json.dumps(row, separators=(",", ":")) for row in chunk)
        response = _post(
            f"{BASE_URL}/uploads/{namespace}/{table_name}/insert",
            data=body.encode("utf-8"),
            headers=_headers(api_key, "application/x-ndjson"),
            timeout=300,
        )
        if response.status_code != 200:
            if sent:
                # The caller cleared this table before inserting, and earlier
                # chunks have already landed. There is no rollback, so the
                # table is now holding part of today's data and nothing else.
                # Say so instead of reporting a plain insert failure, which
                # would read as "nothing was written".
                raise DuneError(
                    f"insert into {namespace}.{table_name} returned HTTP "
                    f"{response.status_code} after {sent} of {len(rows)} rows had "
                    f"already landed; the table is now truncated and must be "
                    f"refilled by a rerun: {response.text[:300]}"
                )
            raise _fail(f"insert into {namespace}.{table_name}", response)
        sent += len(chunk)
    return sent


def execute_query(api_key: str, query_id: int) -> str:
    """Trigger a query run so the dashboard's cached result refreshes.

    Dashboard visitors see the last execution's result; uploading data without
    executing leaves the dashboard showing stale figures.
    """
    response = _post(
        f"{BASE_URL}/query/{query_id}/execute",
        headers=_headers(api_key, "application/json"),
        timeout=60,
    )
    if response.status_code != 200:
        raise _fail(f"execute_query {query_id}", response)
    return response.json().get("execution_id", "")
