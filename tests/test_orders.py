import pytest
import responses

from omniston_dune import omniston, orders

SAMPLE = {
    "lt": "1787654164994885497",
    "status": "TRADE_STATUS_FULLY_FILLED",
    "quote_id": "3f65ab23",
    "input_asset": {"polygon": {"erc20": "0xC011"}},
    "output_asset": {"bnb": {"erc20": "0x55d3"}},
    "quote_input_units": "200000",
    "quote_output_units": "180576479361381644",
    "integrator_fee_pips": 0,
    "protocol_fee_pips": 300,
    "actual_input_units": "200000",
    "actual_output_units": "180522306417573230",
    "actual_protocol_fee_units": "54172943808414",
    "actual_integrator_fee_units": "0",
    "src_trader_address": {"polygon": "0xcd93"},
    "dst_trader_address": {"bnb": "0xcd93"},
    "src_resolver_address": {"polygon": "0x2b65"},
    "dst_resolver_address": {"bnb": "0x2b65"},
    "resolver_id": "EQDE_TwS",
    "quote_request_time": "1787653757",
    "quote_time": "1787654117",
    "order_create_time": "1787654124",
    "order_finalize_time": "1787654161",
}


def test_lt_from_timestamp_is_nanoseconds():
    assert orders.lt_from_timestamp(1787654164) == 1787654164_000_000_000


@responses.activate
def test_iter_orders_paginates_until_has_next_page_is_false():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="10")], "has_next_page": True}},
    )
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="20")], "has_next_page": False}},
    )
    result = list(orders.iter_orders(0, 100))
    assert [o["lt"] for o in result] == ["10", "20"]


@responses.activate
def test_iter_orders_omits_prev_lt_on_the_first_page_then_sends_it():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="10")], "has_next_page": True}},
    )
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "result": {"orders": [], "has_next_page": False}},
    )
    list(orders.iter_orders(0, 100))
    assert "prev_lt" not in responses.calls[0].request.body.decode()
    assert '"prev_lt": "10"' in responses.calls[1].request.body.decode()


@responses.activate
def test_iter_orders_stops_on_an_empty_result_with_no_orders_key():
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    assert list(orders.iter_orders(0, 100)) == []


@responses.activate
def test_iter_orders_raises_if_the_cursor_does_not_advance():
    # Live data shows `lt` strictly ascending with no duplicates across 1000
    # consecutive orders, so this has not been observed against the real
    # service. The guard exists so that a service regression (has_next_page
    # true paired with a stalled or regressed cursor) fails loudly instead of
    # looping forever and hanging a nightly ingest run.
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="10")], "has_next_page": True}},
    )
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="10")], "has_next_page": True}},
    )
    with pytest.raises(omniston.OmnistonError):
        list(orders.iter_orders(0, 100))


def test_flatten_order_computes_the_latency_funnel():
    row = orders.flatten_order(SAMPLE)
    assert row["t_quote"] == 360.0      # 1787654117 - 1787653757
    assert row["t_decide"] == 7.0       # 1787654124 - 1787654117
    assert row["t_settle"] == 37.0      # 1787654161 - 1787654124
    assert row["t_total"] == 404.0      # 1787654161 - 1787653757


def test_flatten_order_splits_assets_and_addresses():
    row = orders.flatten_order(SAMPLE)
    assert row["src_chain_id"] == "polygon"
    assert row["dst_chain_id"] == "bnb"
    assert row["input_asset_kind"] == "erc20"
    assert row["output_asset_address"] == "0x55d3"
    assert row["src_trader_address"] == "0xcd93"


def test_flatten_order_keeps_raw_units_as_strings():
    # These are up to 256-bit; float would silently lose precision.
    row = orders.flatten_order(SAMPLE)
    assert row["actual_output_units"] == "180522306417573230"
    assert isinstance(row["actual_output_units"], str)


def test_flatten_order_tolerates_missing_quote_output_units():
    # Absent on ~25% of orders, all from one resolver on TON->EVM routes.
    partial = dict(SAMPLE)
    del partial["quote_output_units"]
    row = orders.flatten_order(partial)
    assert row["quote_output_units"] is None


def test_flatten_order_tolerates_a_missing_integrator():
    # Absent on ~43% of orders; present exactly when the integrator fee is > 0.
    row = orders.flatten_order(SAMPLE)
    assert row["integrator_address"] is None
    assert row["integrator_chain"] is None
