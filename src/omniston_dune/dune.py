from __future__ import annotations

import json
from collections.abc import Sequence

import requests

BASE_URL = "https://api.dune.com/api/v1"

# /insert accepts up to 1.2GB per request. This project's largest table is a few
# megabytes, so chunking is a guard rail rather than a necessity.
CHUNK_SIZE = 50_000


class DuneError(RuntimeError):
    """A non-success response from the Dune API."""


def _headers(api_key: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"X-DUNE-API-KEY": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _fail(action: str, response: requests.Response) -> DuneError:
    return DuneError(f"{action} returned HTTP {response.status_code}: {response.text[:300]}")


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
    conflict path is the normal path after the first run.
    """
    response = requests.post(
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
    if response.status_code == 409 or "already exists" in response.text.lower():
        return {"already_exists": True}
    raise _fail(f"create_table {namespace}.{table_name}", response)


def clear_table(api_key: str, namespace: str, table_name: str) -> None:
    """Empty a table. Dune cannot delete a date range, only the whole table."""
    response = requests.post(
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
        response = requests.post(
            f"{BASE_URL}/uploads/{namespace}/{table_name}/insert",
            data=body.encode("utf-8"),
            headers=_headers(api_key, "application/x-ndjson"),
            timeout=300,
        )
        if response.status_code != 200:
            raise _fail(f"insert into {namespace}.{table_name}", response)
        sent += len(chunk)
    return sent


def execute_query(api_key: str, query_id: int) -> str:
    """Trigger a query run so the dashboard's cached result refreshes.

    Dashboard visitors see the last execution's result; uploading data without
    executing leaves the dashboard showing stale figures.
    """
    response = requests.post(
        f"{BASE_URL}/query/{query_id}/execute",
        headers=_headers(api_key, "application/json"),
        timeout=60,
    )
    if response.status_code != 200:
        raise _fail(f"execute_query {query_id}", response)
    return response.json().get("execution_id", "")
