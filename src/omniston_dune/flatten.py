from __future__ import annotations


def flatten_asset(asset: dict | None) -> tuple[str | None, str | None, str | None]:
    """Split `{"ton": {"jetton": "EQ..."}}` into ("ton", "jetton", "EQ...").

    The chain name is whatever key the service used; it is never validated
    against a fixed list, because live data contains chains absent from the
    published protobuf.

    `AssetId` is a protobuf `oneof`, so its JSON encoding always carries
    exactly one chain key -- taking the first key is not a guess. The
    nested `kind` is likewise a `oneof`, whose variants are `Empty`
    (`{}`), a bare string, or a nested message, so `value` is always a
    string or a dict; there is no other case to branch on.
    """
    if not asset:
        return (None, None, None)

    chain = next(iter(asset))
    inner = asset.get(chain) or {}
    if not inner:
        return (chain, None, None)

    kind = next(iter(inner))
    value = inner[kind]

    if isinstance(value, dict):
        # `native` is an empty object; the 1155 standards carry two fields.
        contract = value.get("contract_address")
        if contract is None:
            return (chain, kind, None)
        return (chain, kind, f"{contract}:{value.get('token_id', '')}")

    return (chain, kind, value)


def flatten_chain_address(value: dict | None) -> tuple[str | None, str | None]:
    """Split `{"polygon": "0xabc"}` into ("polygon", "0xabc")."""
    if not value:
        return (None, None)
    chain = next(iter(value))
    return (chain, value[chain])
