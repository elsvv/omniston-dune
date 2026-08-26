from omniston_dune import cubes, orders, schemas


def test_every_cube_has_a_schema():
    assert set(schemas.CUBE_COLUMNS) == set(cubes.CUBE_SPECS)


def test_tables_includes_the_orders_table():
    assert "omniston_orders" in schemas.TABLES
    assert len(schemas.TABLES) == 7


def test_only_documented_dune_types_are_used():
    # Dune uploads accept timestamp, double, string, uint256. There is no varchar.
    allowed = {"timestamp", "double", "string", "uint256"}
    for table, columns in schemas.TABLES.items():
        for column in columns:
            assert column["type"] in allowed, f"{table}.{column['name']}"


def test_no_column_name_starts_with_a_digit_or_special_character():
    for table, columns in schemas.TABLES.items():
        for column in columns:
            assert column["name"][0].isalpha(), f"{table}.{column['name']}"


def test_raw_unit_columns_are_strings_to_preserve_256_bit_precision():
    by_name = {c["name"]: c for c in schemas.ORDERS_COLUMNS}
    assert by_name["actual_output_units"]["type"] == "string"
    assert by_name["quote_input_units"]["type"] == "string"


def test_usd_and_latency_columns_are_doubles():
    by_name = {c["name"]: c for c in schemas.ORDERS_COLUMNS}
    assert by_name["t_total"]["type"] == "double"
    chainpair = {c["name"]: c for c in schemas.CUBE_COLUMNS["omniston_daily_chainpair"]}
    assert chainpair["filled_orders_volume_usd"]["type"] == "double"


def test_output_asset_cube_carries_no_volume_column():
    # Both USD metrics are input-side; volume by bought asset does not exist.
    names = {c["name"] for c in schemas.CUBE_COLUMNS["omniston_daily_output_asset"]}
    assert "filled_orders_volume_usd" not in names
    assert "finalized_orders_count" in names


def test_output_asset_cube_carries_fees_and_wallet_count():
    # Fees are collected in the output asset (both `protocol_fees_usd` and
    # `integrator_fees_usd`), and `unique_trader_wallets_count` at this grain
    # answers "how many distinct wallets bought this asset". Only the two
    # input-side volume metrics are excluded.
    names = {c["name"] for c in schemas.CUBE_COLUMNS["omniston_daily_output_asset"]}
    assert "protocol_fees_usd" in names
    assert "integrator_fees_usd" in names
    assert "unique_trader_wallets_count" in names
    assert "finalized_orders_volume_usd" not in names
    assert "filled_orders_volume_usd" not in names


def test_project_keeps_schema_columns_and_fills_gaps():
    schema = [{"name": "a", "type": "string"}, {"name": "b", "type": "double"}]
    assert schemas.project({"a": "x", "z": 9}, schema) == {"a": "x", "b": None}


def test_normalise_row_produces_every_column_each_cube_schema_declares():
    # A rename in cubes.py that this test didn't already catch would break
    # nothing in the unit suite -- it would only fail against the live Dune
    # API, once an upload rejected a row missing a column it declared.
    row = {
        "time_period": "2026-08-25T00:00:00Z",
        "src_chain_id": "ton",
        "dst_chain_id": "bnb",
        "status": "ORDER_STATUS_FINALIZED",
        "resolver_id": "EQDE_TwS",
        "input_asset": {"ton": {"jetton": "EQAi"}},
        "output_asset": {"bnb": {"erc20": "0x55d3"}},
        "integrator_address": {"ton": "EQAj"},
        "finalized_orders_volume_usd": "100",
        "filled_orders_volume_usd": "90",
        "protocol_fees_usd": "1.5",
        "integrator_fees_usd": "0.5",
        "finalized_orders_count": "3",
        "unique_trader_wallets_count": "2",
    }
    out = cubes.normalise_row(row)
    for cube, columns in schemas.CUBE_COLUMNS.items():
        for column in columns:
            assert column["name"] in out, f"{cube}.{column['name']}"


def test_flatten_order_keys_match_orders_columns_exactly():
    # A rename in orders.py that this test didn't already catch would break
    # nothing in the unit suite -- it would only fail against the live Dune
    # API, once an upload rejected a row carrying (or missing) a column.
    order = {
        "lt": "1787654164994885497",
        "status": "TRADE_STATUS_FULLY_FILLED",
        "quote_id": "3f65ab23",
        "input_asset": {"polygon": {"erc20": "0xC011"}},
        "output_asset": {"bnb": {"erc20": "0x55d3"}},
        "quote_input_units": "200000",
        "quote_output_units": "180576479361381644",
        "integrator_fee_pips": 50,
        "protocol_fee_pips": 300,
        "actual_input_units": "200000",
        "actual_output_units": "180522306417573230",
        "actual_protocol_fee_units": "54172943808414",
        "actual_integrator_fee_units": "9026115468678",
        "src_trader_address": {"polygon": "0xcd93"},
        "dst_trader_address": {"bnb": "0xcd93"},
        "src_resolver_address": {"polygon": "0x2b65"},
        "dst_resolver_address": {"bnb": "0x2b65"},
        "integrator_address": {"ton": "EQAi"},
        "resolver_id": "EQDE_TwS",
        "quote_request_time": "1787653757",
        "quote_time": "1787654117",
        "order_create_time": "1787654124",
        "order_finalize_time": "1787654161",
    }
    row = orders.flatten_order(order)
    expected = {c["name"] for c in schemas.ORDERS_COLUMNS}
    assert set(row.keys()) == expected
