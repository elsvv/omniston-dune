from __future__ import annotations

from collections.abc import Sequence

from . import cubes


def _col(name: str, type_: str, nullable: bool = True) -> dict:
    return {"name": name, "type": type_, "nullable": nullable}


_DAY = [_col("day", "timestamp", nullable=False)]

# Which run wrote this row. Every row of every table in one run carries the
# identical value, so a run that dies partway -- leaving tables one to three on
# today's data and five to seven on yesterday's -- is visible from SQL instead
# of being undetectable. It is formatted exactly like the API's own
# `time_period` values, so `run_ts` and `day` parse identically in DuneSQL.
# Dune upload schemas are immutable: a column not added now cannot be added
# later without deleting and recreating the table.
_RUN_TS = [_col("run_ts", "timestamp", nullable=False)]

_METRIC_COLUMNS = [_col(metric, "double") for metric in cubes.ALL_METRICS]

# `omniston_daily_output_asset` excludes only the two input-side volume
# metrics: both volume figures are computed on the input side of a trade, so a
# volume attributed to the bought asset would be a different quantity wearing
# the same label. Everything else belongs here, including the two fee
# metrics -- the protocol collects both `protocol_fees_usd` and
# `integrator_fees_usd` in the output asset, so grouping them by output asset
# answers "which assets earn the fees".
_INPUT_SIDE_VOLUME_METRICS = {"finalized_orders_volume_usd", "filled_orders_volume_usd"}
_OUTPUT_ASSET_METRICS = [
    _col(metric, "double") for metric in cubes.ALL_METRICS if metric not in _INPUT_SIDE_VOLUME_METRICS
]

# Carried by every cube that groups by anything at all. Same-chain swaps are
# the bulk of Omniston's volume, so without the pair a cube cannot separate
# them from cross-chain ones and quietly reports the wrong business.
_PAIR_COLUMNS = [_col("src_chain_id", "string"), _col("dst_chain_id", "string")]

CUBE_COLUMNS: dict[str, list[dict]] = {
    "omniston_daily_total": _DAY + _RUN_TS + _METRIC_COLUMNS,
    "omniston_daily_chainpair": _DAY
    + _RUN_TS
    + _PAIR_COLUMNS
    + [_col("status", "string")]
    + _METRIC_COLUMNS,
    "omniston_daily_resolver": _DAY
    + _RUN_TS
    + _PAIR_COLUMNS
    + [_col("resolver_id", "string"), _col("status", "string")]
    + _METRIC_COLUMNS,
    "omniston_daily_input_asset": _DAY
    + _RUN_TS
    + _PAIR_COLUMNS
    + [
        _col("input_asset_chain", "string"),
        _col("input_asset_kind", "string"),
        _col("input_asset_address", "string"),
    ]
    + _METRIC_COLUMNS,
    "omniston_daily_output_asset": _DAY
    + _RUN_TS
    + _PAIR_COLUMNS
    + [
        _col("output_asset_chain", "string"),
        _col("output_asset_kind", "string"),
        _col("output_asset_address", "string"),
    ]
    + _OUTPUT_ASSET_METRICS,
    "omniston_daily_integrator": _DAY
    + _RUN_TS
    + _PAIR_COLUMNS
    + [_col("integrator_chain", "string"), _col("integrator_address", "string")]
    + _METRIC_COLUMNS,
}

ORDERS_COLUMNS: list[dict] = [
    _col("lt", "string", nullable=False),
    *_RUN_TS,
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
