from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence

import requests

from . import cubes, dune, orders, schemas
from .config import Settings

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    """A table was cleared but not fully refilled."""


def build_datasets(
    settings: Settings,
    *,
    now_ts: int | None = None,
    session: requests.Session | None = None,
) -> dict[str, list[dict]]:
    """Fetch every table's rows. Performs no writes.

    This is a full refresh of all history on every run. The dataset is small
    enough that this is cheap, and it makes retroactive corrections — orders
    that finalize days after they were created — self-healing.
    """
    now_ts = int(time.time()) if now_ts is None else now_ts
    start_ts = settings.history_start_ts

    datasets: dict[str, list[dict]] = {}

    for table_name, dimensions in cubes.CUBE_SPECS.items():
        schema = schemas.CUBE_COLUMNS[table_name]
        raw = cubes.fetch_cube(start_ts, now_ts, dimensions, session=session)
        datasets[table_name] = [
            schemas.project(cubes.normalise_row(row), schema) for row in raw
        ]
        log.info("fetched %s: %d rows", table_name, len(datasets[table_name]))

    datasets["omniston_orders"] = [
        schemas.project(orders.flatten_order(order), schemas.ORDERS_COLUMNS)
        for order in orders.iter_orders(start_ts, now_ts, session=session)
    ]
    log.info("fetched omniston_orders: %d rows", len(datasets["omniston_orders"]))

    return datasets


def validate_datasets(datasets: dict[str, list[dict]]) -> None:
    """Reject nulls in non-nullable columns, across every table, writing nothing.

    Dune rejects the whole insert if a non-nullable column receives null, and
    it rejects it AFTER the table has been cleared. Checking table by table
    inside the publish loop is not enough: a bad row in the last table would
    be found only once the earlier tables had already been created, cleared
    and refilled, leaving Dune holding a mix of today's data and the previous
    run's. This runs as a pre-pass, before the first Dune call of any kind.
    """
    for table_name, rows in datasets.items():
        schema = schemas.TABLES[table_name]
        required = [c["name"] for c in schema if not c.get("nullable", True)]
        for index, row in enumerate(rows):
            missing = [name for name in required if row.get(name) is None]
            if missing:
                raise PublishError(
                    f"{table_name} row {index} has null in non-nullable "
                    f"column(s) {missing}; refusing to clear the table"
                )


def publish(
    settings: Settings,
    datasets: dict[str, list[dict]],
    *,
    dune_module=dune,
) -> dict[str, int]:
    """Create, clear and refill each table. Only called once fetching succeeded."""
    validate_datasets(datasets)

    written: dict[str, int] = {}
    for table_name, rows in datasets.items():
        schema = schemas.TABLES[table_name]
        dune_module.create_table(
            settings.dune_api_key,
            settings.dune_namespace,
            table_name,
            schema,
            description=f"Omniston history: {table_name}",
        )
        dune_module.clear_table(
            settings.dune_api_key, settings.dune_namespace, table_name
        )
        sent = dune_module.insert_rows(
            settings.dune_api_key, settings.dune_namespace, table_name, rows
        )
        if sent != len(rows):
            # The table was cleared immediately before this insert, so a short
            # write leaves it truncated -- neither empty nor intact. Say so
            # explicitly; a silent shortfall would understate the dashboard
            # until the next successful run. Against the real client this line
            # is unreachable: dune.insert_rows raises DuneError naming the
            # truncation rather than returning a short count. It is kept as a
            # cheap guard on the dune_module seam.
            raise PublishError(
                f"{table_name}: cleared, then inserted {sent} of {len(rows)} rows. "
                f"The table is now truncated and must be refilled by a rerun."
            )
        written[table_name] = sent
        log.info("published %s: %d rows", table_name, written[table_name])
    return written


def run(
    settings: Settings,
    *,
    now_ts: int | None = None,
    query_ids: Sequence[int] | Iterable[int] = (),
    dune_module=dune,
) -> dict[str, int]:
    """Full refresh, then refresh the dashboard's cached query results."""
    session = requests.Session()
    try:
        datasets = build_datasets(settings, now_ts=now_ts, session=session)
    finally:
        session.close()

    written = publish(settings, datasets, dune_module=dune_module)

    for query_id in query_ids:
        dune_module.execute_query(settings.dune_api_key, query_id)
        log.info("triggered query %s", query_id)

    return written
