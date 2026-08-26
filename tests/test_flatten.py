from omniston_dune import flatten


def test_ton_native():
    assert flatten.flatten_asset({"ton": {"native": {}}}) == ("ton", "native", None)


def test_ton_jetton():
    asset = {"ton": {"jetton": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"}}
    assert flatten.flatten_asset(asset) == (
        "ton",
        "jetton",
        "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
    )


def test_evm_erc20():
    asset = {"bnb": {"erc20": "0x55d398326f99059fF775485246999027B3197955"}}
    assert flatten.flatten_asset(asset) == (
        "bnb",
        "erc20",
        "0x55d398326f99059fF775485246999027B3197955",
    )


def test_tron_trc20():
    asset = {"tron": {"trc20": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"}}
    assert flatten.flatten_asset(asset) == (
        "tron",
        "trc20",
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    )


def test_unknown_chain_passes_through():
    # `xlayer` is live but absent from the published protobuf. Nothing may
    # hardcode the chain list, or new chains vanish silently.
    asset = {"xlayer": {"erc20": "0xdeadbeef"}}
    assert flatten.flatten_asset(asset) == ("xlayer", "erc20", "0xdeadbeef")


def test_multi_token_standard_joins_contract_and_token_id():
    asset = {"ethereum": {"erc1155": {"contract_address": "0xabc", "token_id": "42"}}}
    assert flatten.flatten_asset(asset) == ("ethereum", "erc1155", "0xabc:42")


def test_missing_asset_is_all_none():
    assert flatten.flatten_asset(None) == (None, None, None)
    assert flatten.flatten_asset({}) == (None, None, None)


def test_chain_address():
    value = {"polygon": "0xcd93B163292D4848D35Df2bBC0e9c3ebe3991614"}
    assert flatten.flatten_chain_address(value) == (
        "polygon",
        "0xcd93B163292D4848D35Df2bBC0e9c3ebe3991614",
    )


def test_missing_chain_address_is_none():
    # integrator_address is absent on ~43% of orders.
    assert flatten.flatten_chain_address(None) == (None, None)
