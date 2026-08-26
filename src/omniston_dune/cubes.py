from __future__ import annotations

from collections.abc import Iterator, Sequence

import requests

from . import omniston
from .flatten import flatten_asset, flatten_chain_address

DAY = "TIME_GROUPING_DAY"

# The service caps a filtered range at 31 days. 30 leaves margin for the
# inclusive/exclusive boundary.
WINDOW_DAYS = 30

ALL_METRICS = [
    "finalized_orders_volume_usd",
    "filled_orders_volume_usd",
    "protocol_fees_usd",
    "integrator_fees_usd",
    "finalized_orders_count",
    "unique_trader_wallets_count",
]

# `unique_trader_wallets_count` is NOT additive across buckets. It is only
# meaningful in the cube whose grouping matches how it will be displayed, which
# is why `omniston_daily_total` exists as its own table.
#
# Every cube except that one carries the chain pair. Omniston settles same-chain
# swaps as well as cross-chain ones, and same-chain is the larger business by
# volume -- so a cube without the pair cannot answer a cross-chain question at
# all, and silently answers a different one instead. Measured cost of carrying
# it: roughly three times the rows on the resolver and integrator cubes, half
# again on the asset cubes, which is a few thousand rows across all history.
_PAIR = ["src_chain_id", "dst_chain_id"]

CUBE_SPECS: dict[str, list[str]] = {
    "omniston_daily_total": [],
    "omniston_daily_chainpair": [*_PAIR, "status"],
    "omniston_daily_resolver": [*_PAIR, "resolver_id", "status"],
    "omniston_daily_input_asset": [*_PAIR, "input_asset"],
    "omniston_daily_output_asset": [*_PAIR, "output_asset"],
    "omniston_daily_integrator": [*_PAIR, "integrator_address"],
}

_NESTED_ASSET_DIMENSIONS = ("input_asset", "output_asset")
_NESTED_ADDRESS_DIMENSIONS = (
    "src_trader_address",
    "dst_trader_address",
    "src_resolver_address",
    "dst_resolver_address",
    "integrator_address",
)


def iter_windows(
    start_ts: int, end_ts: int, window_days: int = WINDOW_DAYS
) -> Iterator[tuple[int, int]]:
    """Tile [start_ts, end_ts) into contiguous windows within the 31-day cap."""
    step = window_days * 86400
    cursor = start_ts
    while cursor < end_ts:
        nxt = min(cursor + step, end_ts)
        yield (cursor, nxt)
        cursor = nxt


def fetch_rows(
    from_ts: int,
    to_ts: int,
    dimensions: Sequence[str],
    metrics: Sequence[str] | None = None,
    *,
    time_grouping: str | None = DAY,
    session: requests.Session | None = None,
) -> list[dict]:
    """One aggregate call. Returns raw rows, possibly empty."""
    requested = list(metrics) if metrics else list(ALL_METRICS)
    # A row is dropped when every requested metric is zero. Keeping the count in
    # the request guarantees a row exists whenever any order exists.
    if "finalized_orders_count" not in requested:
        requested.append("finalized_orders_count")

    params: dict = {
        "filters": [
            {"time_range": {"from_timestamp": str(from_ts), "to_timestamp": str(to_ts)}}
        ],
        "aggregates_list": {"values": requested},
    }
    if time_grouping:
        params["time_grouping"] = time_grouping
    if dimensions:
        params["dimensions"] = {"values": list(dimensions)}

    result = omniston.call(omniston.AGGREGATES_METHOD, params, session=session)
    return result.get("rows", [])


def fetch_cube(
    start_ts: int,
    end_ts: int,
    dimensions: Sequence[str],
    metrics: Sequence[str] | None = None,
    *,
    session: requests.Session | None = None,
) -> list[dict]:
    """Walk the whole range in legal windows and concatenate the raw rows."""
    rows: list[dict] = []
    for from_ts, to_ts in iter_windows(start_ts, end_ts):
        rows.extend(
            fetch_rows(from_ts, to_ts, dimensions, metrics, session=session)
        )
    return rows


def normalise_row(row: dict) -> dict:
    """Flatten nested dimensions and cast decimal-string metrics to floats."""
    out: dict = {"day": row.get("time_period")}

    for key in ("src_chain_id", "dst_chain_id", "resolver_id", "status"):
        out[key] = row.get(key)

    for key in _NESTED_ASSET_DIMENSIONS:
        chain, kind, address = flatten_asset(row.get(key))
        out[f"{key}_chain"] = chain
        out[f"{key}_kind"] = kind
        out[f"{key}_address"] = address

    for key in _NESTED_ADDRESS_DIMENSIONS:
        chain, address = flatten_chain_address(row.get(key))
        prefix = key.removesuffix("_address")
        out[f"{prefix}_chain"] = chain
        out[f"{prefix}_address"] = address

    for metric in ALL_METRICS:
        value = row.get(metric)
        out[metric] = float(value) if value not in (None, "") else 0.0

    return out
