"""Regenerate sql/README.md from the dashboard layout.

The query table is derived, not hand-kept, so a chart that moves sections or a
query that is added cannot leave the index quietly wrong. Run after build.py,
which is what fills in the query IDs.

Usage:  python dashboard/gen_readme.py
"""

import json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build

# Sections come from the layout itself, so the table cannot drift from the
# dashboard the way a hand-kept list does.
section, seen = "Headline", {}
for kind, key, *_ in build.layout():
    if kind == "text":
        heading = re.search(r"^##\s+(.+)$", key, re.M)
        if heading:
            section = heading.group(1).strip()
    else:
        seen.setdefault(key.split("::")[0], section)

m = json.loads(pathlib.Path("dashboard/manifest.json").read_text())
rows = "\n".join(f"| {m['queries'][k]} | `{k}.sql` | {sec} |"
                 for k, sec in seen.items() if k in m["queries"])

pathlib.Path("sql/README.md").write_text(f"""# Dashboard SQL

Each file is the source of one saved Dune query. Dune is the deployment target,
not the source of truth — edit here, then run `python dashboard/build.py`, which
pushes the SQL, the visualizations and the dashboard layout in one pass and
records the IDs in `dashboard/manifest.json`.

Dashboard: https://dune.com/elsvv/omniston-cross-chain

| Query ID | File | Dashboard section |
| --- | --- | --- |
{rows}

## Scope: cross-chain only

Every query filters `src_chain_id != dst_chain_id`. Omniston settles same-chain
swaps as well, and by volume they are the larger business — $41M of the $43M
settled to date — so a query that omits the filter silently reports that other
business instead. The gap is not a rounding error: it is the difference between
$1.7M and $43M.

This is why every cube except `omniston_daily_total` carries `src_chain_id` and
`dst_chain_id` as dimensions. Without them the filter cannot be written at all,
and a fee or asset figure drawn from such a cube describes swaps the dashboard
is not about.

Dune upload schemas are immutable, so adding those two dimensions meant deleting
and recreating the affected tables:

    curl -X DELETE -H "X-DUNE-API-KEY: $DUNE_API_KEY" \\
      https://api.dune.com/api/v1/table/$DUNE_NAMESPACE/omniston_daily_integrator

then re-running `python -m omniston_dune`, which is a full refresh anyway. Space
the deletes out — they count against the same write limit as everything else.

## What the data says that the documentation does not

**The service's daily aggregates bucket by order creation, not settlement.**
Verified by comparing a month of daily counts both ways: create-time buckets
matched the cube exactly, finalize-time buckets were off by sixteen orders.
Anything charted beside a cube-derived series has to agree with that.

**Every cross-chain input is a token and a dollar stablecoin.** Not one native
coin appears on the input side. That is what makes a per-trade dollar value
computable from raw units and decimals with no price feed: 12,893 of 12,931
settled swaps price this way, summing to $1,717,949 against the cube's
$1,718,632, a gap of 0.04%.

**An average over history describes a protocol that no longer exists.** The
hour-of-day and weekday charts average over the last eight whole weeks, not all
time. Cross-chain barely traded before June; included, those weeks pull every
hour toward zero and the chart answers "what did an average hour look like since
launch" instead of what one looks like now. Hours and days with no swaps are
carried through the average as zeros — dropped instead, an hour that is dead six
days in seven reports the average of its one busy day and ranks among the
busiest of the clock.

**Settled and finalized are different populations and must not be divided by
each other.** `filled_orders_volume_usd` counts only swaps that settled;
`finalized_orders_count` counts settled plus failed. Around 7% of cross-chain
orders fail, so a ratio that mixes the two is wrong by that much. Every query
here now pairs like with like.

## Reading the options off a dashboard that already works

Dune's API will hand you the full option JSON of any public dashboard, not just
your own:

    dune dashboard get --owner <handle> --slug <slug> -o json
    dune visualization get <visualization_id> -o json

The first returns the grid and the visualization IDs, the second the exact
`options` object behind each chart. Every option key documented below was read
this way or tested by rendering, because Dune's own documentation lists almost
none of them. When a chart here looks like it cannot be built, check whether
somebody has already built it.

## What Dune's chart options actually do

**A cumulative line needs the second y-axis.** Put `yAxis: 1` on the series and
add a second object to the chart's `yAxis` array. Without it the running total
and the daily bars share one scale, and since the total ends up orders of
magnitude larger, the bars flatten onto the baseline and the chart shows a line
above an empty floor. Label the second axis: an unlabelled one leaves no way to
tell which scale a line is drawn against.

**`series.showTotal`** writes the sum above each stacked column. It is
undocumented and it works.

**`numberFormat` and the axis `tickFormat` are separate.** The first formats
tooltips and data labels, the second the axis ticks; neither inherits from the
other, so a chart with only one set will show `$1.2M` on the axis and `1200000`
in the tooltip.

**The percent tick format multiplies by a hundred.** Feed `tickFormat: "0%"` a
column already expressed in percent and the axis reads `10000%`. Emit fractions.

**Normalising to 100% is done in SQL here.** `normalizeToPercentage`,
`series.percentValues` and `series.stacking: "percent"` were each tried and none
produced a normalised chart. Note that `whale_hunter/stonfi` runs
`percentValues: true` together with `stacking: "stack"` in production, which is
a combination not tested here — if a share chart is wanted without the SQL, that
pairing is where to start.

**The cohort visualisation renders blank** for every option shape tried —
Redash-style `dateColumn`/`stageColumn`/`valueColumn`/`totalColumn` and a
`columnMapping` of the same roles. Retention is drawn as one line per cohort
instead.

## A note on writing to Dune

The free tier allows 15 write requests per minute and the CLI does not pace
itself, so creating several queries or visualizations back to back returns
`429 Too many requests`. `build.py` spaces writes 6 seconds apart; the ingest
pipeline uses the same interval.

Query *executions* are capped separately, on how many may be in flight at once.
Firing twenty with `--no-wait` returns `429` for most of them, which reads as
"the query is broken" when it is not. `build.py` therefore runs them one at a
time and waits for each.

## Identifying Polymarket

Polymarket settles in its own branded stablecoin, `pUSD`
(`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` on Polygon, 6 decimals), confirmed
against Dune's `tokens.erc20`. The token is therefore the identifier: pUSD as
the output asset is a deposit, pUSD as the input asset is a withdrawal. No proxy
wallet or bytecode heuristic is needed, and since pUSD is a dollar stablecoin
the USD figures come straight from the raw units rather than a price feed.
""")
print("wrote sql/README.md with", rows.count("\n") + 1, "rows")
