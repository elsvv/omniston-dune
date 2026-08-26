from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

import requests

from . import cubes, dune, orders, schemas
from .config import Settings

log = logging.getLogger(__name__)

# The API stamps its own `time_period` values in this format, and `day` carries
# them through unchanged.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# `omniston_daily_total` carries one row per UTC day, so consecutive `day`
# values are normally one day apart. Two tolerates a single genuinely empty
# day. Anything wider means a fetch window came back with nothing -- and under
# a full refresh that is not a harmless hole in the data: publish clears every
# table before refilling it from exactly these rows, so the missing period is
# about to be deleted from the published tables.
MAX_DAY_GAP = 2


class PublishError(RuntimeError):
    """A table was cleared but not fully refilled."""


def format_timestamp(ts: int) -> str:
    """Format a Unix timestamp the way the API formats its own `time_period`."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(TIMESTAMP_FORMAT)


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

    # One timestamp for the whole run, captured before the first fetch and
    # stamped onto every row of every table. Without it, a Dune-side failure
    # that lands after table four leaves tables one to three holding today's
    # data and five to seven holding yesterday's, with no way to tell from SQL.
    run_ts = format_timestamp(now_ts)

    datasets: dict[str, list[dict]] = {}

    for table_name, dimensions in cubes.CUBE_SPECS.items():
        schema = schemas.CUBE_COLUMNS[table_name]
        raw = cubes.fetch_cube(start_ts, now_ts, dimensions, session=session)
        datasets[table_name] = [
            schemas.project({**cubes.normalise_row(row), "run_ts": run_ts}, schema)
            for row in raw
        ]
        log.info("fetched %s: %d rows", table_name, len(datasets[table_name]))

    datasets["omniston_orders"] = [
        schemas.project(
            {**orders.flatten_order(order), "run_ts": run_ts}, schemas.ORDERS_COLUMNS
        )
        for order in orders.iter_orders(start_ts, now_ts, session=session)
    ]
    log.info("fetched omniston_orders: %d rows", len(datasets["omniston_orders"]))

    return datasets


def _parse_day(table_name: str, value: object) -> datetime:
    """Parse a `day` value, or say which table and value could not be read."""
    try:
        return datetime.strptime(str(value), TIMESTAMP_FORMAT)
    except (TypeError, ValueError) as exc:
        raise PublishError(
            f"{table_name} has an unparseable day value {value!r}; expected "
            f"the API's own {TIMESTAMP_FORMAT} form. Day coverage cannot be "
            f"checked, so this run is refused rather than published blind."
        ) from exc


def _check_upstream_returned_data(datasets: dict[str, list[dict]]) -> None:
    """Refuse to publish a dataset that upstream never really filled.

    This is the spec's "alert on an unexpected drop", and the failure it
    prevents is a green run that blanks the dashboard. An empty Omniston
    response is `{"result": {}}`, which is indistinguishable from "no data
    exists". If the service answers 200 with an empty result for every window,
    every table here is an empty list, the null pre-pass above passes
    vacuously, publish clears all seven tables, insert_rows returns 0 without
    an HTTP call, publish's `sent == len(rows)` check passes as `0 == 0`, and
    the run exits 0 having wiped seven public tables.

    The same mechanism at partial strength is quieter and worse: one 30-day
    window returning empty deletes a month of history with no signal at all,
    which is why day coverage is checked and not just emptiness.

    Tables the schema does not know are left alone: publish looks their schema
    up unconditionally, so they can never reach Dune in the first place.
    """
    for table_name, rows in datasets.items():
        if table_name in schemas.TABLES and not rows:
            raise PublishError(
                f"{table_name} came back empty (0 rows). Every table is fully "
                f"refreshed, so publishing this would clear the table and "
                f"leave it empty; refusing to publish."
            )

    total = datasets.get("omniston_daily_total")
    if not total:
        return

    days = sorted(_parse_day("omniston_daily_total", row.get("day")) for row in total)
    for earlier, later in zip(days, days[1:]):
        gap = (later - earlier).days
        if gap > MAX_DAY_GAP:
            raise PublishError(
                f"omniston_daily_total jumps {gap} days from "
                f"{earlier.strftime(TIMESTAMP_FORMAT)} to "
                f"{later.strftime(TIMESTAMP_FORMAT)}, more than MAX_DAY_GAP="
                f"{MAX_DAY_GAP}; a fetch window returned nothing and that "
                f"period would be deleted from the published tables. "
                f"{len(days)} day rows in total; refusing to publish."
            )


def validate_datasets(datasets: dict[str, list[dict]]) -> None:
    """Reject nulls in non-nullable columns, across every table, writing nothing.

    Dune rejects the whole insert if a non-nullable column receives null, and
    it rejects it AFTER the table has been cleared. Checking table by table
    inside the publish loop is not enough: a bad row in the last table would
    be found only once the earlier tables had already been created, cleared
    and refilled, leaving Dune holding a mix of today's data and the previous
    run's. This runs as a pre-pass, before the first Dune call of any kind.

    The upstream sanity gate lives here too, rather than in publish, so that it
    is enforced before any Dune call and so `--dry-run` exercises it as well.
    """
    for table_name, rows in datasets.items():
        schema = schemas.TABLES.get(table_name)
        if schema is None:
            # publish looks the schema up unconditionally, so an unknown table
            # name raises there and can never reach Dune. Nothing to check.
            continue
        required = [c["name"] for c in schema if not c.get("nullable", True)]
        for index, row in enumerate(rows):
            missing = [name for name in required if row.get(name) is None]
            if missing:
                raise PublishError(
                    f"{table_name} row {index} has null in non-nullable "
                    f"column(s) {missing}; refusing to clear the table"
                )

    _check_upstream_returned_data(datasets)


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
            # until the next successful run. This line IS reachable against the
            # real client: insert_rows sums the `rows_written` Dune reports, so
            # a 2xx response that acknowledges fewer rows than were sent lands
            # here rather than raising.
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
