import pytest
import responses

from omniston_dune import cubes, omniston


def test_iter_windows_never_exceeds_the_31_day_cap():
    start, end = 0, 100 * 86400
    windows = list(cubes.iter_windows(start, end))
    assert windows[0][0] == start
    assert windows[-1][1] == end
    for from_ts, to_ts in windows:
        assert to_ts - from_ts <= 31 * 86400
    # Windows must tile the range with no gaps.
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[1] == later[0]


def test_iter_windows_handles_a_range_shorter_than_one_window():
    assert list(cubes.iter_windows(1000, 2000)) == [(1000, 2000)]


def test_iter_windows_is_empty_when_start_is_not_before_end():
    assert list(cubes.iter_windows(5000, 5000)) == []


@responses.activate
def test_fetch_rows_always_requests_the_order_count():
    # Rows whose requested metrics are all zero are dropped by the server, so a
    # volume-only request silently loses chain pairs that had only failures.
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    cubes.fetch_rows(0, 100, ["src_chain_id"], metrics=["filled_orders_volume_usd"])
    body = responses.calls[0].request.body
    sent = body.decode() if isinstance(body, bytes) else body
    assert "finalized_orders_count" in sent


@responses.activate
def test_fetch_rows_survives_an_empty_result():
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    assert cubes.fetch_rows(0, 100, ["src_chain_id"]) == []


@responses.activate
def test_fetch_cube_concatenates_across_windows():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "result": {"rows": [{"src_chain_id": "ton"}]}},
    )
    rows = cubes.fetch_cube(0, 90 * 86400, ["src_chain_id"])
    assert len(responses.calls) == 3
    assert len(rows) == 3


def test_normalise_row_flattens_dimensions_and_casts_metrics():
    row = {
        "time_period": "2026-08-25T00:00:00Z",
        "src_chain_id": "bnb",
        "input_asset": {"bnb": {"erc20": "0x55d3"}},
        "integrator_address": {"ton": "EQAi"},
        "filled_orders_volume_usd": "110313.9388981324101512987747",
        "finalized_orders_count": "123",
    }
    out = cubes.normalise_row(row)
    assert out["day"] == "2026-08-25T00:00:00Z"
    assert out["src_chain_id"] == "bnb"
    assert out["input_asset_chain"] == "bnb"
    assert out["input_asset_kind"] == "erc20"
    assert out["input_asset_address"] == "0x55d3"
    assert out["integrator_chain"] == "ton"
    assert out["integrator_address"] == "EQAi"
    assert out["filled_orders_volume_usd"] == pytest.approx(110313.93889813241)
    assert out["finalized_orders_count"] == 123.0
    assert "time_period" not in out


def test_normalise_row_defaults_absent_metrics_to_zero():
    out = cubes.normalise_row({"src_chain_id": "ton", "finalized_orders_count": "4"})
    assert out["filled_orders_volume_usd"] == 0.0
    assert out["protocol_fees_usd"] == 0.0
