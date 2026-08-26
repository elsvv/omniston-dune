from __future__ import annotations

import requests

from . import config

JSONRPC_URL = "https://omni-history.ston.fi/json-rpc"

LIST_METHOD = "stonfi.omni.history.v1.FinalizedOrdersRpc.List"
AGGREGATES_METHOD = "stonfi.omni.history.v1.AggregatesRpc.FinalizedOrderAggregates"


class OmnistonError(RuntimeError):
    """A JSON-RPC error payload, or a transport-level failure."""


def new_session() -> requests.Session:
    return requests.Session()


def call(
    method: str,
    params: dict,
    *,
    session: requests.Session | None = None,
    user_agent: str = config.USER_AGENT,
    timeout: float = 60.0,
) -> dict:
    """Invoke one JSON-RPC method and return its `result` object.

    The result may legitimately be an empty dict: the service omits `rows` and
    `orders` entirely when nothing matched. Callers must use `.get(key, [])`.

    A timeout is always set. Two filters (`resolver_id_in_list` and
    `integrator_address_in_list`) hang forever on the live service, and without
    a timeout an ingest run would stall indefinitely.
    """
    poster = session.post if session is not None else requests.post
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    headers = {"Content-Type": "application/json", "User-Agent": user_agent}

    try:
        response = poster(JSONRPC_URL, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise OmnistonError(f"{method} transport failure: {exc}") from exc

    if response.status_code != 200:
        raise OmnistonError(
            f"{method} returned HTTP {response.status_code}: {response.text[:200]}"
        )

    body = response.json()
    if "error" in body:
        raise OmnistonError(f"{method} failed: {body['error'].get('message', body['error'])}")

    return body.get("result", {})
