"""Build the Omniston dashboard on Dune from the SQL in sql/.

Dune is the deployment target, not the source of truth: the SQL and the layout
live here, and this script pushes them. Query and visualization IDs are kept in
manifest.json so a rerun updates what exists instead of creating duplicates.

Writes are paced. Dune's free tier allows 15 write requests a minute and the
CLI does not pace itself, so an unpaced build reliably earns a 429 partway
through and leaves the dashboard half-assembled.

Usage:  python dashboard/build.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
MANIFEST = ROOT / "dashboard" / "manifest.json"

# 15 writes/minute on the free tier. 4.5s is 13/minute, which sounds safe and
# is not: the limit is a rolling window shared with whatever else has just
# touched the account -- an ingest run, a table delete -- so a build started
# soon after one still earns a 429. 6s is 10/minute, the same pace the ingest
# pipeline uses, and costs about a minute on a full build.
WRITE_INTERVAL = 6.0
RATE_LIMIT_WAIT = 45.0
MAX_RETRIES = 3

_last_write = 0.0


def run(args: list[str], *, write: bool = True) -> str:
    """Invoke the Dune CLI, pacing writes and retrying a 429."""
    global _last_write
    for attempt in range(MAX_RETRIES + 1):
        if write:
            gap = time.monotonic() - _last_write
            if gap < WRITE_INTERVAL:
                time.sleep(WRITE_INTERVAL - gap)
        proc = subprocess.run(["dune", *args], capture_output=True, text=True)
        if write:
            _last_write = time.monotonic()
        combined = proc.stdout + proc.stderr
        if proc.returncode == 0:
            return proc.stdout
        if "429" in combined and attempt < MAX_RETRIES:
            print(f"    rate limited, waiting {RATE_LIMIT_WAIT:.0f}s")
            time.sleep(RATE_LIMIT_WAIT)
            continue
        raise SystemExit(f"dune {' '.join(args[:2])} failed:\n{combined.strip()}")
    raise SystemExit("unreachable")


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"dashboard_id": None, "queries": {}, "visualizations": {}}


def save_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n")


def sync_query(m: dict, key: str, name: str, description: str, dry: bool) -> int:
    sql = (SQL_DIR / f"{key}.sql").read_text()
    existing = m["queries"].get(key)
    if dry:
        print(f"  query {key}: {'update ' + str(existing) if existing else 'create'}")
        return existing or 0
    if existing:
        run(["query", "update", str(existing), "--sql", sql,
             "--name", name, "--description", description])
        print(f"  query {key}: updated {existing}")
        return existing
    out = run(["query", "create", "--name", name,
               "--description", description, "--sql", sql, "-o", "json"])
    qid = json.loads(out)["query_id"]
    m["queries"][key] = qid
    save_manifest(m)
    print(f"  query {key}: created {qid}")
    return qid


def sync_viz(m: dict, query_key: str, qid: int, suffix: str,
             name: str, vtype: str, options: dict, dry: bool) -> int:
    key = f"{query_key}::{suffix}"
    existing = m["visualizations"].get(key)
    opts = json.dumps(options, separators=(",", ":"))
    if dry:
        print(f"    viz {key}: {'update ' + str(existing) if existing else 'create'}")
        return existing or 0
    if existing:
        run(["visualization", "update", str(existing),
             "--name", name, "--options", opts])
        print(f"    viz {key}: updated {existing}")
        return existing
    # A previous run may have created this and died before recording the ID.
    # Adopt by name rather than creating a duplicate.
    adopted = find_viz_by_name(qid, name)
    if adopted:
        run(["visualization", "update", str(adopted), "--name", name, "--options", opts])
        m["visualizations"][key] = adopted
        save_manifest(m)
        print(f"    viz {key}: adopted {adopted}")
        return adopted

    out = run(["visualization", "create", "--query-id", str(qid),
               "--name", name, "--type", vtype, "--options", opts, "-o", "json"])
    vid = parse_viz_id(out)
    m["visualizations"][key] = vid
    save_manifest(m)
    print(f"    viz {key}: created {vid}")
    return vid


def parse_viz_id(out: str) -> int:
    """The CLI reports the new ID as `id` in JSON, or in a sentence in text mode."""
    try:
        body = json.loads(out)
        for field in ("id", "visualization_id"):
            if field in body:
                return int(body[field])
    except json.JSONDecodeError:
        pass
    match = re.search(r"visualization (\d+)", out)
    if match:
        return int(match.group(1))
    raise SystemExit(f"could not read a visualization id from:\n{out}")


def find_viz_by_name(qid: int, name: str) -> int | None:
    out = run(["visualization", "list", "--query-id", str(qid), "-o", "json"],
              write=False)
    try:
        for row in json.loads(out).get("results", []):
            if row.get("name") == name:
                return int(row["id"])
    except json.JSONDecodeError:
        pass
    return None


def counter(col: str, label: str, prefix: str = "", decimals: int = 0,
            suffix: str = "") -> dict:
    return {
        "counterColName": col, "rowNumber": 1, "stringDecimal": decimals,
        "stringPrefix": prefix, "stringSuffix": suffix, "counterLabel": label,
        "coloredPositiveValues": False, "coloredNegativeValues": False,
    }


def chart(kind: str, x: str, series: list[tuple[str, str, str]],
          y_title: str = "", tick: str | None = None,
          stacking: str | None = None, sort_x: bool = True,
          series_col: str | None = None, x_title: str = "") -> dict:
    """series: (column, display name, one of column|line|area).

    `series_col` names a category column that splits one value column into a
    band per category — Dune calls that role "series", and leaving it implicit
    makes the chart depend on Dune guessing correctly.
    """
    mapping = {x: "x"}
    if series_col:
        mapping[series_col] = "series"
    options = {}
    for col, display, stype in series:
        mapping[col] = "y"
        options[col] = {"type": stype, "name": display, "yAxis": 0, "zIndex": 0}
    y_axis: dict = {"title": {"text": y_title}}
    if tick:
        y_axis["tickFormat"] = tick
    return {
        "globalSeriesType": kind, "sortX": sort_x,
        "legend": {"enabled": len(series) > 1 or bool(series_col)},
        "series": {"stacking": stacking},
        "xAxis": {"title": {"text": x_title}},
        "yAxis": [y_axis],
        "columnMapping": mapping, "seriesOptions": options,
    }


def pie(category: str, value: str) -> dict:
    """A share-of-total donut. Slice order comes from the query, not the label."""
    return {
        "globalSeriesType": "pie", "sortX": False, "showDataLabels": True,
        "legend": {"enabled": True}, "series": {"stacking": None},
        "columnMapping": {category: "x", value: "y"},
        "seriesOptions": {value: {"type": "pie", "yAxis": 0, "zIndex": 0}},
    }


def table(columns: list[tuple[str, str]], per_page: int = 20) -> dict:
    return {
        "itemsPerPage": per_page,
        "columns": [
            {"name": c, "title": t, "type": "normal",
             "alignContent": "left", "isHidden": False}
            for c, t in columns
        ],
    }


INTRO = """# Omniston cross-chain

Swap between TON, Ethereum, BNB, Base, Arbitrum, Polygon and Tron — in about half a minute, \
at a quote that holds.

### → [Start a cross-chain swap](https://app.ston.fi/swap?mode=cross-chain)

Built from the public [History API](https://docs.ston.fi/developer-section/omniston/history). \
Refreshed daily · [source](https://github.com/elsvv/omniston-dune)"""

LOGO = """![STON.fi](https://static.ston.fi/logo/ston_symbol.png)"""

FEE_NOTE = """## What a swap costs

**About two basis points — two cents on a hundred dollars.**

Two fees ride on a swap: one for the app that brought the trade, one for the protocol. On \
cross-chain swaps almost all of it is the protocol's. Apps charge real money on same-chain \
swaps and have barely begun to charge for these."""

POLYMARKET_NOTE = """## Polymarket

**Most of the traffic, a minority of the money.**

Polymarket transfers are small — the median is single digits — so they are the majority of \
cross-chain swaps and well under half the volume. Polymarket gives every user their own \
deposit wallet and settles in its own stablecoin, so these are counted exactly rather than \
guessed, with no address heuristics involved.

Wagered volume is gross, not profit: $100 recycled through forty bets counts as $4,000."""

METHODOLOGY = """### Notes

Every figure here counts cross-chain swaps only — source chain different from destination. \
Omniston settles same-chain swaps too, and by volume they are the larger business, so \
including them would answer a different question than the one this dashboard asks.

Volume is what settled, not what was quoted. A failed order delivers nothing and costs the \
trader nothing, so failures are shown under reliability rather than counted as volume.

Timings are percentiles. The tail is long enough that an average would describe a swap almost \
nobody gets.

Volume by asset means what was sold — that is the side the protocol prices, which is why what \
traders buy is counted in swaps instead.

[Swap](https://app.ston.fi/swap?mode=cross-chain) · \
[API docs](https://docs.ston.fi/developer-section/omniston/history) · \
[source](https://github.com/elsvv/omniston-dune)"""


# key, title, description, [(suffix, name, type, options)]
def spec() -> list[tuple]:
    return [
        ("headline", "Omniston · headline", "", [
            ("volume",    "Cross-chain volume",   "counter", counter("volume_usd", "Cross-chain volume", "$")),
            ("swaps",     "Cross-chain swaps",    "counter", counter("swaps", "Cross-chain swaps")),
            ("traders",   "Unique traders",       "counter", counter("traders", "Unique traders")),
            ("netton",    "Net flow into TON",    "counter", counter("net_ton_usd", "Net flow into TON", "$")),
            ("median",    "Median swap, seconds", "counter", counter("median_seconds", "Median swap", "", 0, "s")),
            ("success",   "Settled successfully", "counter", counter("success_pct", "Settled successfully", "", 1, "%")),
        ]),
        ("cross_chain_volume_daily", "Omniston · Daily cross-chain volume",
         "Settled volume per day, with a 7-day average.", [
            ("chart", "Daily cross-chain volume", "chart",
             chart("column", "day", [("volume_usd", "Volume", "column"),
                                     ("volume_7d_avg", "7-day average", "line")], "USD", "$0.0a")),
        ]),
        ("chain_volume_daily", "Omniston · Volume by destination chain",
         "Which chains are growing, band by band.", [
            ("chart", "Volume by destination chain", "chart",
             chart("area", "day", [("volume_usd", "Volume", "area")], "USD", "$0.0a",
                   stacking="normal", series_col="chain")),
        ]),
        ("new_vs_returning_weekly", "Omniston · New against returning traders",
         "Traders appearing for the first time, against those coming back.", [
            ("chart", "New against returning traders", "chart",
             chart("column", "week", [("new_traders", "New", "column"),
                                      ("returning_traders", "Returning", "column")],
                   "Traders", stacking="stack")),
        ]),
        ("trader_cohorts", "Omniston · Trader retention cohorts",
         "Each line is one week's new traders, followed forward.", [
            ("chart", "Trader retention by cohort", "chart",
             chart("line", "weeks_after", [("retained", "Retained", "line")],
                   "Still swapping", "0%", series_col="cohort",
                   x_title="Weeks after first swap")),
        ]),
        ("fee_headline", "Omniston · Fee headline", "", [
            ("rate",  "Fee on a swap", "counter",
             counter("take_rate_bps", "Fee on a swap", "", 1, " bps")),
            ("total", "Paid in fees",  "counter",
             counter("total_fees_usd", "Paid in fees", "$", 0)),
            ("apps",  "Apps sending flow", "counter",
             counter("integrators", "Apps sending flow")),
        ]),
        ("fee_split_daily", "Omniston · Fees earned per day",
         "Split between the integrator that brought the trade and the protocol.", [
            ("chart", "Fees earned per day", "chart",
             chart("column", "day", [("integrator_fees_usd", "Integrator", "column"),
                                     ("protocol_fees_usd", "Protocol", "column")],
                   "USD", "$0.0a", stacking="stack")),
        ]),
        ("integrator_league", "Omniston · Integrator league table",
         "Which apps send cross-chain flow, and what they charge for it.", [
            ("table", "Integrator league table", "table",
             table([("integrator", "Integrator"), ("volume_usd", "Volume $"),
                    ("swaps", "Swaps"), ("avg_swap_usd", "Avg swap $"),
                    ("fees_earned_usd", "Fees earned $"),
                    ("take_rate_bps", "Take rate, bps")], 12)),
        ]),
        ("chain_flows_sankey", "Omniston · Chain-to-chain flow",
         "Where value moves between chains.", [
            ("sankey", "Chain-to-chain flow", "sankey",
             {"columnMapping": {"source": "source", "target": "target", "value": "value"}}),
        ]),
        ("net_flow_by_chain", "Omniston · Net capital flow by chain",
         "Above the line the chain gained value; below it, the chain lost value.", [
            ("chart", "Net flow by chain", "chart",
             chart("column", "chain", [("net_usd", "Net flow", "column")],
                   "USD", "$0.0a", sort_x=False)),
        ]),
        ("corridor_share_daily", "Omniston · Corridor share of volume",
         "Which routes carry the flow, day by day.", [
            ("chart", "Corridor share of volume", "chart",
             chart("column", "day", [("share", "Share", "column")],
                   "Share of daily volume", "0%", stacking="normal",
                   series_col="corridor")),
        ]),
        ("hourly_clock", "Omniston · Swaps by hour of day",
         "When cross-chain demand actually happens, UTC.", [
            ("chart", "Swaps by hour of day", "chart",
             chart("column", "hour_utc", [("swaps", "Swaps", "column")], "Swaps")),
        ]),
        ("latency_funnel", "Omniston · Latency funnel",
         "Percentiles for each stage from quote request to settlement.", [
            ("chart", "Latency funnel", "chart",
             chart("bar", "stage", [("p50", "p50", "column"), ("p90", "p90", "column"),
                                    ("p99", "p99", "column")], "Seconds", sort_x=False)),
        ]),
        ("settlement_speed_daily", "Omniston · Settlement speed over time",
         "Is it getting faster? Median and p90 seconds, daily.", [
            ("chart", "Settlement speed over time", "chart",
             chart("line", "day", [("p50_seconds", "Median", "line"),
                                   ("p90_seconds", "p90", "line")], "Seconds")),
        ]),
        ("resolver_league", "Omniston · Resolver league table",
         "Resolvers specialise: compare average trade size, not just volume.", [
            ("table", "Resolver league table", "table",
             table([("resolver", "Resolver"), ("volume_usd", "Volume $"),
                    ("orders", "Orders"), ("avg_trade_usd", "Avg trade $"),
                    ("success_pct", "Success %"),
                    ("median_settle_seconds", "Median settle s"),
                    ("traders", "Traders")], 10)),
        ]),
        ("assets_sold", "Omniston · What traders sell",
         "Share of settled volume, by the asset sold.", [
            ("pie", "What traders sell", "chart", pie("asset", "volume_usd")),
        ]),
        ("assets_bought", "Omniston · What traders buy",
         "Share of swaps, by the asset bought.", [
            ("pie", "What traders buy", "chart", pie("asset", "swaps")),
        ]),
        ("trade_size_distribution", "Omniston · Trade size distribution",
         "Where the orders are, against where the money is.", [
            ("chart", "Trade size distribution", "chart",
             chart("bar", "trade_size", [("orders", "Orders", "column")], "Orders", sort_x=False)),
        ]),
        ("polymarket_impact", "Omniston · Polymarket impact",
         "What the funded wallets went on to do.", [
            ("wagered",  "Wagered on Polymarket", "counter", counter("wagered_usd", "Wagered on Polymarket", "$")),
            ("traders",  "New traders created",   "counter", counter("new_traders", "New traders created")),
            ("multiple", "Wagered per $1 deposited", "counter", counter("multiple", "Wagered per $1 deposited", "$", 1, "x")),
            ("lag",      "Deposit to first bet",  "counter", counter("median_lag_minutes", "Deposit to first bet", "", 0, " min")),
        ]),
        ("polymarket_headline", "Omniston · Polymarket headline", "", [
            ("deposits",    "Into Polymarket",   "counter", counter("deposits_usd", "Into Polymarket", "$")),
            ("withdrawals", "Out of Polymarket", "counter", counter("withdrawals_usd", "Out of Polymarket", "$")),
            ("wallets",     "Wallets funded",    "counter", counter("wallets", "Wallets funded")),
            ("median",      "Median transfer",   "counter", counter("median_deposit_usd", "Median transfer", "$")),
            ("share",       "Share of cross-chain swaps", "counter",
             counter("share_of_swaps_pct", "Share of cross-chain swaps", "", 0, "%")),
            ("volshare",    "Share of cross-chain volume", "counter",
             counter("share_of_volume_pct", "Share of cross-chain volume", "", 0, "%")),
        ]),
        ("polymarket_flows_daily", "Omniston · Polymarket flows",
         "Deposits and withdrawals per day, with the net trend.", [
            ("chart", "Polymarket flows", "chart",
             chart("column", "day", [("deposits_usd", "Deposits", "column"),
                                     ("withdrawals_usd", "Withdrawals", "column"),
                                     ("net_7d_avg", "Net, 7-day average", "line")], "USD", "$0.0a")),
        ]),
    ]


# (kind, key, width, height) — the grid is 6 columns wide
def layout() -> list[tuple]:
    return [
        ("text", INTRO, 4, 5),
        ("text", LOGO, 2, 5),
        ("viz", "headline::volume", 2, 4), ("viz", "headline::swaps", 2, 4),
        ("viz", "headline::traders", 2, 4),
        ("viz", "headline::netton", 2, 4), ("viz", "headline::median", 2, 4),
        ("viz", "headline::success", 2, 4),
        ("text", "## Growth", 6, 1),
        ("viz", "cross_chain_volume_daily::chart", 6, 7),
        ("viz", "chain_volume_daily::chart", 6, 7),
        ("viz", "new_vs_returning_weekly::chart", 6, 7),
        ("viz", "trader_cohorts::chart", 6, 7),
        ("text", FEE_NOTE, 6, 3),
        ("viz", "fee_headline::rate", 2, 4),
        ("viz", "fee_headline::total", 2, 4),
        ("viz", "fee_headline::apps", 2, 4),
        ("viz", "fee_split_daily::chart", 6, 7),
        ("viz", "integrator_league::table", 6, 7),
        ("text", "## Where the money moves", 6, 1),
        ("viz", "chain_flows_sankey::sankey", 6, 9),
        ("viz", "corridor_share_daily::chart", 6, 7),
        ("viz", "net_flow_by_chain::chart", 6, 7),
        ("viz", "hourly_clock::chart", 6, 6),
        ("text", "## How fast it settles", 6, 1),
        ("viz", "latency_funnel::chart", 3, 7),
        ("viz", "settlement_speed_daily::chart", 3, 7),
        ("text", "## Who settles the trades", 6, 1),
        ("viz", "resolver_league::table", 6, 6),
        ("text", "## What people trade", 6, 1),
        ("viz", "assets_sold::pie", 3, 7),
        ("viz", "assets_bought::pie", 3, 7),
        ("viz", "trade_size_distribution::chart", 6, 6),
        ("text", POLYMARKET_NOTE, 6, 6),
        ("viz", "polymarket_impact::wagered", 3, 4),
        ("viz", "polymarket_impact::traders", 3, 4),
        ("viz", "polymarket_impact::multiple", 3, 4),
        ("viz", "polymarket_impact::lag", 3, 4),
        ("viz", "polymarket_headline::deposits", 2, 4),
        ("viz", "polymarket_headline::withdrawals", 2, 4),
        ("viz", "polymarket_headline::wallets", 2, 4),
        ("viz", "polymarket_headline::median", 2, 4),
        ("viz", "polymarket_headline::share", 2, 4),
        ("viz", "polymarket_headline::volshare", 2, 4),
        ("viz", "polymarket_flows_daily::chart", 6, 8),
        ("text", METHODOLOGY, 6, 4),
    ]


def pack(entries: list[tuple], viz_ids: dict) -> tuple[list[dict], list[dict]]:
    """Place widgets left to right across a 6-column grid, wrapping as needed."""
    viz_widgets: list[dict] = []
    text_widgets: list[dict] = []
    row = col = row_height = 0

    for kind, key, width, height in entries:
        if col + width > 6:
            row += row_height
            col = row_height = 0
        position = {"row": row, "col": col, "size_x": width, "size_y": height}
        if kind == "text":
            text_widgets.append({"text": key, "position": position})
        else:
            vid = viz_ids.get(key)
            if vid is None:
                print(f"  layout: skipping {key}, no visualization was created")
                continue
            viz_widgets.append({"visualization_id": vid, "position": position})
        col += width
        row_height = max(row_height, height)

    return viz_widgets, text_widgets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing to Dune.")
    args = ap.parse_args()

    m = load_manifest()
    failures: list[str] = []

    for key, name, description, visuals in spec():
        print(f"{key}")
        qid = sync_query(m, key, name, description, args.dry_run)
        for suffix, vname, vtype, options in visuals:
            try:
                sync_viz(m, key, qid, suffix, vname, vtype, options, args.dry_run)
            except SystemExit as exc:
                # One bad visualization must not cost the whole build.
                print(f"    viz {key}::{suffix}: FAILED, continuing")
                failures.append(f"{key}::{suffix}: {exc}")

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    viz_widgets, text_widgets = pack(layout(), m["visualizations"])
    print(f"\ncomposing dashboard: {len(viz_widgets)} charts, {len(text_widgets)} text blocks")
    run(["dashboard", "update", str(m["dashboard_id"]),
         "--name", "Omniston Cross-Chain",
         # Pinned: Dune regenerates the slug from the name otherwise, which
         # silently breaks every link anyone has already shared.
         "--slug", "omniston-cross-chain",
         "--visualization-widgets", json.dumps(viz_widgets, separators=(",", ":")),
         "--text-widgets", json.dumps(text_widgets, separators=(",", ":"))])
    save_manifest(m)

    # A dashboard visitor sees the last cached execution, not a live query, so a
    # freshly built dashboard shows "Click Run to get results" until each query
    # has been executed once.
    # Executions are run one at a time and waited on. The free tier caps how
    # many can be in flight at once, so firing twenty with --no-wait earns a
    # 429 for most of them -- which reads as "the query is broken" when it is
    # not. Waiting also surfaces a query that runs but returns nothing, which
    # renders as an empty tile rather than an error.
    print("\nexecuting queries so the dashboard shows results")
    for key, *_ in spec():
        qid = m["queries"].get(key)
        if qid is None:
            continue
        try:
            out = run(["query", "run", str(qid), "--limit", "1", "-o", "json"])
            rows = json.loads(out).get("result", {}).get("rows", [])
        except SystemExit as exc:
            print(f"  {key}: execute FAILED")
            failures.append(f"{key}: execute failed: {exc}")
            continue
        if rows:
            print(f"  {key}: ok")
        else:
            print(f"  {key}: ran but returned no rows")
            failures.append(f"{key}: query returned no rows")

    if failures:
        print(f"\n{len(failures)} step(s) failed:")
        for f in failures:
            print(f"  - {f.splitlines()[0]}")
        return 1
    print("\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
