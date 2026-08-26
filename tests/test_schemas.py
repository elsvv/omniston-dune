from omniston_dune import cubes, schemas


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


def test_project_keeps_schema_columns_and_fills_gaps():
    schema = [{"name": "a", "type": "string"}, {"name": "b", "type": "double"}]
    assert schemas.project({"a": "x", "z": 9}, schema) == {"a": "x", "b": None}
