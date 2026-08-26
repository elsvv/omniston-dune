import pytest

from omniston_dune import config, pipeline, schemas

SETTINGS = config.Settings(
    dune_api_key="key", dune_namespace="me", user_agent="ua/1", history_start_ts=1000
)


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
    monkeypatch.setattr(
        pipeline.cubes, "fetch_cube", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down"))
    )
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))
    fake = FakeDune()
    with pytest.raises(RuntimeError, match="api down"):
        pipeline.run(SETTINGS, now_ts=2000, dune_module=fake)
    assert fake.calls == []


def test_publish_creates_clears_then_inserts_in_that_order(monkeypatch):
    fake = FakeDune()
    pipeline.publish(SETTINGS, {"omniston_daily_total": [{"day": "x"}]}, dune_module=fake)
    assert fake.calls == [
        ("create", "omniston_daily_total"),
        ("clear", "omniston_daily_total"),
        ("insert", "omniston_daily_total"),
    ]


def test_publish_refuses_a_null_in_a_non_nullable_column_before_clearing():
    # `day` and `lt` are declared non-nullable. Dune would reject the insert
    # after the clear had already run, leaving the table empty.
    fake = FakeDune()
    with pytest.raises(pipeline.PublishError, match="non-nullable"):
        pipeline.publish(
            SETTINGS, {"omniston_daily_total": [{"day": None}]}, dune_module=fake
        )
    assert fake.calls == []


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
            {"omniston_daily_total": [{"day": "x"}, {"day": "y"}]},
            dune_module=fake,
        )


def test_run_executes_the_dashboard_queries_last(monkeypatch):
    monkeypatch.setattr(pipeline.cubes, "fetch_cube", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))
    fake = FakeDune()
    pipeline.run(SETTINGS, now_ts=2000, query_ids=(11, 22), dune_module=fake)
    assert fake.calls[-2:] == [("execute", 11), ("execute", 22)]
