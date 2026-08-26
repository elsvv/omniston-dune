from __future__ import annotations

from collections.abc import Sequence

from . import cubes


def _col(name: str, type_: str, nullable: bool = True) -> dict:
    return {"name": name, "type": type_, "nullable": nullable}


_DAY = [_col("day", "timestamp", nullable=False)]

_METRIC_COLUMNS = [_col(metric, "double") for metric in cubes.ALL_METRICS]

# `omniston_daily_output_asset` deliberately omits every USD metric: both volume
# figures are computed on the input side, so a volume attributed to the bought
# asset would be a different quantity wearing the same label.
_COUNT_ONLY = [_col("finalized_orders_count", "double")]

CUBE_COLUMNS: dict[str, list[dict]] = {
    "omniston_daily_total": _DAY + _METRIC_COLUMNS,
    "omniston_daily_chainpair": _DAY
    + [_col("src_chain_id", "string"), _col("dst_chain_id", "string"), _col("status", "string")]
    + _METRIC_COLUMNS,
    "omniston_daily_resolver": _DAY
    + [_col("resolver_id", "string"), _col("status", "string")]
    + _METRIC_COLUMNS,
    "omniston_daily_input_asset": _DAY
    + [
        _col("input_asset_chain", "string"),
        _col("input_asset_kind", "string"),
        _col("input_asset_address", "string"),
    ]
    + _METRIC_COLUMNS,
    "omniston_daily_output_asset": _DAY
    + [
        _col("output_asset_chain", "string"),
        _col("output_asset_kind", "string"),
        _col("output_asset_address", "string"),
    ]
    + _COUNT_ONLY,
    "omniston_daily_integrator": _DAY
    + [_col("integrator_chain", "string"), _col("integrator_address", "string")]
    + _METRIC_COLUMNS,
}

ORDERS_COLUMNS: list[dict] = [
    _col("lt", "string", nullable=False),
    _col("status", "string"),
    _col("quote_id", "string"),
    _col("src_chain_id", "string"),
    _col("dst_chain_id", "string"),
    _col("input_asset_chain", "string"),
    _col("input_asset_kind", "string"),
    _col("input_asset_address", "string"),
    _col("output_asset_chain", "string"),
    _col("output_asset_kind", "string"),
    _col("output_asset_address", "string"),
    _col("quote_input_units", "string"),
    _col("quote_output_units", "string"),
    _col("actual_input_units", "string"),
    _col("actual_output_units", "string"),
    _col("actual_protocol_fee_units", "string"),
    _col("actual_integrator_fee_units", "string"),
    _col("integrator_fee_pips", "double"),
    _col("protocol_fee_pips", "double"),
    _col("resolver_id", "string"),
    _col("src_trader_chain", "string"),
    _col("src_trader_address", "string"),
    _col("dst_trader_chain", "string"),
    _col("dst_trader_address", "string"),
    _col("src_resolver_chain", "string"),
    _col("src_resolver_address", "string"),
    _col("dst_resolver_chain", "string"),
    _col("dst_resolver_address", "string"),
    _col("integrator_chain", "string"),
    _col("integrator_address", "string"),
    _col("quote_request_time", "double"),
    _col("quote_time", "double"),
    _col("order_create_time", "double"),
    _col("order_finalize_time", "double"),
    _col("t_quote", "double"),
    _col("t_decide", "double"),
    _col("t_settle", "double"),
    _col("t_total", "double"),
]

TABLES: dict[str, list[dict]] = {**CUBE_COLUMNS, "omniston_orders": ORDERS_COLUMNS}


def project(row: dict, schema: Sequence[dict]) -> dict:
    """Keep exactly the schema's columns; absent keys become None.

    Dune rejects rows carrying keys the table does not have, and the cube
    normaliser deliberately emits a superset so one function serves every cube.
    """
    return {column["name"]: row.get(column["name"]) for column in schema}
