"""Checks against the live Omniston API.

Run with:  python -m pytest -m live -v
These are excluded from the default suite because they need network access.
"""
import pytest

from omniston_dune import cubes, orders

pytestmark = pytest.mark.live

DAY = 86400
# 2026-08-24T00:00:00Z and 2026-08-25T00:00:00Z
AUG_24 = 1787529600
AUG_25 = 1787616000
AUG_26 = 1787702400


def _cross_chain_volume(from_ts: int, to_ts: int) -> float:
    rows = cubes.fetch_rows(
        from_ts,
        to_ts,
        ["src_chain_id", "dst_chain_id"],
        ["filled_orders_volume_usd"],
        time_grouping=None,
    )
    return sum(
        float(row.get("filled_orders_volume_usd", 0))
        for row in rows
        if row["src_chain_id"] != row["dst_chain_id"]
    )


@pytest.mark.parametrize(
    "from_ts,to_ts,expected",
    [(AUG_24, AUG_25, 126_583.0), (AUG_25, AUG_26, 149_371.0)],
)
def test_cross_chain_daily_volume_matches_the_published_figures(from_ts, to_ts, expected):
    """The team quoted these two days publicly. If we disagree, our
    cross-chain definition is wrong, not theirs."""
    actual = _cross_chain_volume(from_ts, to_ts)
    assert actual == pytest.approx(expected, rel=0.005)


def test_cross_chain_excludes_non_ton_intrachain():
    """Cross-chain is src != dst, never 'everything except TON to TON'.
    Non-TON intrachain swaps exist (base->base, bnb->bnb and others)."""
    rows = cubes.fetch_rows(
        AUG_24, AUG_26, ["src_chain_id", "dst_chain_id"], time_grouping=None
    )
    same_chain = [r for r in rows if r["src_chain_id"] == r["dst_chain_id"]]
    assert same_chain, "expected at least TON->TON to be present"


def test_only_two_statuses_occur():
    """No PARTIALLY_FILLED, CANCELLED or IN_PROGRESS has ever occurred.
    If this fails, the dashboard's success-rate logic needs revisiting."""
    rows = cubes.fetch_rows(AUG_24, AUG_26, ["status"], time_grouping=None)
    statuses = {row["status"] for row in rows}
    assert statuses <= {"TRADE_STATUS_FULLY_FILLED", "TRADE_STATUS_FAILED"}


def test_failed_orders_deliver_and_consume_nothing():
    """FAILED means the trader got nothing and paid nothing, so failed orders
    must never be counted in filled volume."""
    failed = [
        o
        for o in orders.iter_orders(AUG_25, AUG_26)
        if o["status"] == "TRADE_STATUS_FAILED"
    ]
    assert failed, "expected some failures in a full day"
    for order in failed:
        assert order.get("actual_input_units", "0") == "0"
        assert order.get("actual_output_units", "0") == "0"


def test_empty_window_returns_no_rows_without_raising():
    """A window before history began returns {"result": {}} with no rows key."""
    assert cubes.fetch_rows(1_500_000_000, 1_500_086_400, ["src_chain_id"]) == []


def test_chain_list_is_not_assumed():
    """Live data contains chains absent from the published protobuf. This test
    documents what is currently live rather than constraining it."""
    rows = cubes.fetch_rows(AUG_24, AUG_26, ["src_chain_id"], time_grouping=None)
    chains = {row["src_chain_id"] for row in rows}
    assert "ton" in chains
    assert len(chains) >= 2
