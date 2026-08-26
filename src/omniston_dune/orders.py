from __future__ import annotations

from collections.abc import Iterator

import requests

from . import omniston
from .flatten import flatten_asset, flatten_chain_address

PAGE_SIZE = 1000

# Raw token amounts, kept as strings because they are up to 256-bit.
_UNIT_FIELDS = (
    "quote_input_units",
    "quote_output_units",
    "actual_input_units",
    "actual_output_units",
    "actual_protocol_fee_units",
    "actual_integrator_fee_units",
)


def lt_from_timestamp(ts: int) -> int:
    """`lt` is a nanosecond timestamp anchored to finalization time."""
    return ts * 1_000_000_000


def iter_orders(
    from_ts: int,
    to_ts: int,
    *,
    page_size: int = PAGE_SIZE,
    session: requests.Session | None = None,
) -> Iterator[dict]:
    """Yield every finalized order created within [from_ts, to_ts).

    `time_range` filters on creation time while `lt` orders by finalization
    time. The first page omits `prev_lt` so scanning starts at the beginning of
    the filtered set, which is correct regardless of that skew.
    """
    prev_lt: str | None = None
    while True:
        params: dict = {
            "filters": [
                {"time_range": {"from_timestamp": str(from_ts), "to_timestamp": str(to_ts)}}
            ],
            "limit": page_size,
        }
        if prev_lt is not None:
            params["prev_lt"] = prev_lt

        result = omniston.call(omniston.LIST_METHOD, params, session=session)
        page = result.get("orders", [])
        if not page:
            return

        yield from page

        if not result.get("has_next_page"):
            return
        prev_lt = page[-1]["lt"]


def _seconds(order: dict, key: str) -> float | None:
    value = order.get(key)
    return float(value) if value not in (None, "") else None


def _gap(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return later - earlier


def flatten_order(order: dict) -> dict:
    """One flat row, with the four latency intervals precomputed in seconds."""
    requested = _seconds(order, "quote_request_time")
    quoted = _seconds(order, "quote_time")
    created = _seconds(order, "order_create_time")
    finalized = _seconds(order, "order_finalize_time")

    in_chain, in_kind, in_address = flatten_asset(order.get("input_asset"))
    out_chain, out_kind, out_address = flatten_asset(order.get("output_asset"))

    row: dict = {
        "lt": order["lt"],
        "status": order.get("status"),
        "quote_id": order.get("quote_id"),
        "src_chain_id": in_chain,
        "dst_chain_id": out_chain,
        "input_asset_chain": in_chain,
        "input_asset_kind": in_kind,
        "input_asset_address": in_address,
        "output_asset_chain": out_chain,
        "output_asset_kind": out_kind,
        "output_asset_address": out_address,
        "resolver_id": order.get("resolver_id"),
        "integrator_fee_pips": float(order.get("integrator_fee_pips") or 0),
        "protocol_fee_pips": float(order.get("protocol_fee_pips") or 0),
        "quote_request_time": requested,
        "quote_time": quoted,
        "order_create_time": created,
        "order_finalize_time": finalized,
        "t_quote": _gap(quoted, requested),
        "t_decide": _gap(created, quoted),
        "t_settle": _gap(finalized, created),
        "t_total": _gap(finalized, requested),
    }

    for field in _UNIT_FIELDS:
        value = order.get(field)
        row[field] = str(value) if value not in (None, "") else None

    for field in ("src_trader_address", "dst_trader_address",
                  "src_resolver_address", "dst_resolver_address",
                  "integrator_address"):
        chain, address = flatten_chain_address(order.get(field))
        prefix = field.removesuffix("_address")
        row[f"{prefix}_chain"] = chain
        row[f"{prefix}_address"] = address

    return row
