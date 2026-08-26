import pytest

from omniston_dune import config, pipeline, schemas

SETTINGS = config.Settings(
    dune_api_key="key", dune_namespace="me", user_agent="ua/1", history_start_ts=1000
)

# Two consecutive UTC days, in the API's own `time_period` format.
DAY_1 = "2026-08-24T00:00:00Z"
DAY_2 = "2026-08-25T00:00:00Z"

# `run_ts` is non-nullable in every table, so every fixture row carries one.
RUN_TS = "2026-08-26T03:00:00Z"


class FakeDune:
    def __init__(self, fail_on_insert=False):
        self.calls = []
        self.fail_on_insert = fail_on_insert

    def create_table(self, api_key, namespace, table_name, schema, **kwargs):
        self.calls.append(("create", table_name))
        return {}

    def clear_table(self, api_key, namespace, table_name):
        self.calls.append(("clear", table_name))

    def insert_rows(self, api_key, namespace, table_name, rows, **kwargs):
        if self.fail_on_insert:
            raise RuntimeError("boom")
        self.calls.append(("insert", table_name))
        return len(rows)

    def execute_query(self, api_key, query_id):
        self.calls.append(("execute", query_id))
        return "exec"


def test_build_datasets_produces_one_entry_per_table(monkeypatch):
    monkeypatch.setattr(pipeline.cubes, "fetch_cube", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))
    datasets = pipeline.build_datasets(SETTINGS, now_ts=2000)
    assert set(datasets) == set(schemas.TABLES)


def test_build_datasets_projects_rows_onto_the_schema(monkeypatch):
    raw = {
        "time_period": "2026-08-25T00:00:00Z",
        "src_chain_id": "ton",
        "dst_chain_id": "bnb",
        "status": "TRADE_STATUS_FULLY_FILLED",
        "filled_orders_volume_usd": "12.5",
        "finalized_orders_count": "3",
    }
    monkeypatch.setattr(
        pipeline.cubes,
        "fetch_cube",
        lambda start, end, dims, **k: [raw] if dims == ["src_chain_id", "dst_chain_id", "status"] else [],
    )
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))

    datasets = pipeline.build_datasets(SETTINGS, now_ts=2000)
    row = datasets["omniston_daily_chainpair"][0]
    expected = {c["name"] for c in schemas.CUBE_COLUMNS["omniston_daily_chainpair"]}
    assert set(row) == expected
    assert row["filled_orders_volume_usd"] == 12.5


def test_run_fetches_everything_before_touching_dune(monkeypatch):
    # If a fetch raises, Dune must be untouched — no cleared, empty tables.
    # The LAST fetch is the one that raises: every cube succeeds and only the
    # orders fetch blows up. Raising on the first fetch would prove nothing,
    # since a per-table fetch-then-publish loop would also leave Dune untouched
    # in that case. Here six cube datasets are already in hand, so `calls == []`
    # holds only if publishing waits for every fetch to complete.
    monkeypatch.setattr(pipeline.cubes, "fetch_cube", lambda *a, **k: [])
    monkeypatch.setattr(
        pipeline.orders,
        "iter_orders",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down")),
    )
    fake = FakeDune()
    with pytest.raises(RuntimeError, match="api down"):
        pipeline.run(SETTINGS, now_ts=2000, dune_module=fake)
    assert fake.calls == []


def test_publish_creates_clears_then_inserts_in_that_order(monkeypatch):
    fake = FakeDune()
    pipeline.publish(
        SETTINGS,
        {"omniston_daily_total": [{"day": DAY_1, "run_ts": RUN_TS}]},
        dune_module=fake,
    )
    assert fake.calls == [
        ("create", "omniston_daily_total"),
        ("clear", "omniston_daily_total"),
        ("insert", "omniston_daily_total"),
    ]


def test_publish_refuses_a_null_in_a_non_nullable_column_before_clearing():
    # `day` and `lt` are declared non-nullable. Dune would reject the insert
    # after the clear had already run, leaving the table empty.
    #
    # The bad row sits in the SECOND table on purpose. Validating inside the
    # publish loop passes a single-table version of this test while still
    # creating, clearing and refilling table one before it ever looks at table
    # two — Dune left holding today's data for one table and the previous
    # run's for the other. Nothing may be written at all.
    fake = FakeDune()
    with pytest.raises(pipeline.PublishError) as excinfo:
        pipeline.publish(
            SETTINGS,
            {
                "omniston_daily_total": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_resolver": [
                    {"day": DAY_1, "run_ts": RUN_TS},
                    {"day": None, "run_ts": RUN_TS},
                ],
            },
            dune_module=fake,
        )
    message = str(excinfo.value)
    assert "omniston_daily_resolver" in message
    assert "row 1" in message
    assert "non-nullable" in message and "'day'" in message
    assert fake.calls == []


def test_validate_datasets_accepts_a_clean_mapping():
    pipeline.validate_datasets(
        {
            "omniston_daily_total": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_orders": [{"lt": "1", "status": None, "run_ts": RUN_TS}],
        }
    )


def test_validate_datasets_names_the_table_row_and_columns():
    with pytest.raises(pipeline.PublishError) as excinfo:
        pipeline.validate_datasets(
            {
                "omniston_orders": [
                    {"lt": "1", "run_ts": RUN_TS},
                    {"lt": None, "run_ts": RUN_TS},
                ]
            }
        )
    assert "omniston_orders row 1" in str(excinfo.value)
    assert "'lt'" in str(excinfo.value)


def test_publish_raises_when_fewer_rows_land_than_were_sent():
    # The spec requires verifying row counts after each run: clear-then-insert
    # has no rollback, so a short write leaves a truncated table.
    class ShortWriter(FakeDune):
        def insert_rows(self, api_key, namespace, table_name, rows, **kwargs):
            self.calls.append(("insert", table_name))
            return len(rows) - 1

    fake = ShortWriter()
    with pytest.raises(pipeline.PublishError, match="truncated"):
        pipeline.publish(
            SETTINGS,
            {
                "omniston_daily_total": [
                    {"day": DAY_1, "run_ts": RUN_TS},
                    {"day": DAY_2, "run_ts": RUN_TS},
                ]
            },
            dune_module=fake,
        )


def test_run_executes_the_dashboard_queries_last(monkeypatch):
    # Every table gets a row: seven empty tables is precisely what the sanity
    # gate refuses, so a run that publishes anything must have data.
    monkeypatch.setattr(
        pipeline.cubes,
        "fetch_cube",
        lambda *a, **k: [{"time_period": DAY_1, "finalized_orders_count": "1"}],
    )
    monkeypatch.setattr(
        pipeline.orders,
        "iter_orders",
        lambda *a, **k: iter([{"lt": "1787654164994885497"}]),
    )
    fake = FakeDune()
    pipeline.run(SETTINGS, now_ts=2000, query_ids=(11, 22), dune_module=fake)
    assert fake.calls[-2:] == [("execute", 11), ("execute", 22)]


# --- upstream sanity gate -------------------------------------------------
#
# An empty Omniston result is `{"result": {}}`, indistinguishable from "no data
# exists". Without this gate a service returning 200s with empty results makes
# `build_datasets` yield seven empty lists, `publish` clear all seven tables,
# `insert_rows` return 0 without an HTTP call, and the run exit 0 -- seven
# public tables wiped and the job green. Each fixture below is written out in
# full rather than derived from a shared builder, so a bug in a helper cannot
# make these pass for the wrong reason.


def test_validate_datasets_accepts_a_healthy_full_dataset():
    pipeline.validate_datasets(
        {
            "omniston_daily_total": [
                {"day": DAY_1, "run_ts": RUN_TS},
                {"day": DAY_2, "run_ts": RUN_TS},
            ],
            "omniston_daily_chainpair": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_resolver": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_input_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_output_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_integrator": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_orders": [{"lt": "1787654164994885497", "run_ts": RUN_TS}],
        }
    )


def test_validate_datasets_rejects_an_empty_orders_table():
    with pytest.raises(pipeline.PublishError) as excinfo:
        pipeline.validate_datasets(
            {
                "omniston_daily_total": [
                    {"day": DAY_1, "run_ts": RUN_TS},
                    {"day": DAY_2, "run_ts": RUN_TS},
                ],
                "omniston_daily_chainpair": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_resolver": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_input_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_output_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_integrator": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_orders": [],
            }
        )
    message = str(excinfo.value)
    assert "omniston_orders" in message
    assert "0 rows" in message


def test_validate_datasets_rejects_an_empty_cube():
    # One cube coming back empty while the others are full is the partial case:
    # publish would clear that table and leave it empty.
    with pytest.raises(pipeline.PublishError) as excinfo:
        pipeline.validate_datasets(
            {
                "omniston_daily_total": [
                    {"day": DAY_1, "run_ts": RUN_TS},
                    {"day": DAY_2, "run_ts": RUN_TS},
                ],
                "omniston_daily_chainpair": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_resolver": [],
                "omniston_daily_input_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_output_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_integrator": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_orders": [{"lt": "1787654164994885497", "run_ts": RUN_TS}],
            }
        )
    message = str(excinfo.value)
    assert "omniston_daily_resolver" in message
    assert "0 rows" in message


def test_validate_datasets_rejects_a_gap_in_day_coverage():
    # Aug 24 then Aug 28: the three days in between are missing, so a fetch
    # window returned nothing. Under a full refresh that period is not merely
    # absent from today's load -- it is about to be deleted from the published
    # tables. Every table is non-empty here, so only the day check can catch it.
    with pytest.raises(pipeline.PublishError) as excinfo:
        pipeline.validate_datasets(
            {
                "omniston_daily_total": [
                    {"day": "2026-08-24T00:00:00Z", "run_ts": RUN_TS},
                    {"day": "2026-08-28T00:00:00Z", "run_ts": RUN_TS},
                ],
                "omniston_daily_chainpair": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_resolver": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_input_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_output_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_daily_integrator": [{"day": DAY_1, "run_ts": RUN_TS}],
                "omniston_orders": [{"lt": "1787654164994885497", "run_ts": RUN_TS}],
            }
        )
    message = str(excinfo.value)
    assert "4 days" in message
    assert "2026-08-24T00:00:00Z" in message and "2026-08-28T00:00:00Z" in message
    assert f"MAX_DAY_GAP={pipeline.MAX_DAY_GAP}" in message


def test_validate_datasets_tolerates_a_single_missing_day():
    # A day with genuinely zero orders is plausible; MAX_DAY_GAP exists so that
    # one such day does not fail an otherwise healthy run.
    pipeline.validate_datasets(
        {
            "omniston_daily_total": [
                {"day": "2026-08-24T00:00:00Z", "run_ts": RUN_TS},
                {"day": "2026-08-26T00:00:00Z", "run_ts": RUN_TS},
            ],
            "omniston_daily_chainpair": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_resolver": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_input_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_output_asset": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_daily_integrator": [{"day": DAY_1, "run_ts": RUN_TS}],
            "omniston_orders": [{"lt": "1787654164994885497", "run_ts": RUN_TS}],
        }
    )


def test_every_row_of_every_table_carries_the_same_run_ts(monkeypatch):
    # Dune upload schemas are immutable, so this column had to exist before the
    # first real run. It is what makes a half-finished run visible from SQL: if
    # a failure hits table four, tables one to three hold this run_ts and the
    # rest still hold the previous run's.
    monkeypatch.setattr(
        pipeline.cubes,
        "fetch_cube",
        lambda *a, **k: [
            {"time_period": DAY_1, "finalized_orders_count": "1"},
            {"time_period": DAY_2, "finalized_orders_count": "2"},
        ],
    )
    monkeypatch.setattr(
        pipeline.orders,
        "iter_orders",
        lambda *a, **k: iter(
            [{"lt": "1787654164994885497"}, {"lt": "1787654164994885498"}]
        ),
    )

    # 2026-08-26T03:00:00Z
    datasets = pipeline.build_datasets(SETTINGS, now_ts=1787713200)

    stamps = {row["run_ts"] for rows in datasets.values() for row in rows}
    assert stamps == {"2026-08-26T03:00:00Z"}
    # Same shape as the API's own `time_period`, so both parse identically.
    assert len(datasets["omniston_orders"]) == 2
    assert datasets["omniston_daily_total"][0]["day"] == DAY_1
