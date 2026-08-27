# Dashboard SQL

Each file is the source of one saved Dune query. Dune is the deployment target,
not the source of truth — edit here, then run `python dashboard/build.py`, which
pushes the SQL, the visualizations and the dashboard layout in one pass and
records the IDs in `dashboard/manifest.json`.

Dashboard: https://dune.com/elsvv/omniston-cross-chain

| Query ID | File | Dashboard section |
| --- | --- | --- |
| 8478191 | `headline.sql` | Headline |
| 8477613 | `cross_chain_volume_daily.sql` | Growth |
| 8480436 | `chain_volume_daily.sql` | Growth |
| 8478332 | `new_vs_returning_weekly.sql` | Growth |
| 8485054 | `trader_cohorts.sql` | Growth |
| 8480487 | `fee_headline.sql` | What a swap costs |
| 8480540 | `fee_split_daily.sql` | What a swap costs |
| 8480557 | `integrator_league.sql` | What a swap costs |
| 8478385 | `chain_flows_sankey.sql` | Where the money moves |
| 8485049 | `corridor_share_daily.sql` | Where the money moves |
| 8478339 | `net_flow_by_chain.sql` | Where the money moves |
| 8480620 | `hourly_clock.sql` | Where the money moves |
| 8478399 | `latency_funnel.sql` | How fast it settles |
| 8480677 | `settlement_speed_daily.sql` | How fast it settles |
| 8478465 | `resolver_league.sql` | Who settles the trades |
| 8480706 | `assets_sold.sql` | What people trade |
| 8480715 | `assets_bought.sql` | What people trade |
| 8478514 | `trade_size_distribution.sql` | What people trade |
| 8479423 | `polymarket_impact.sql` | Polymarket |
| 8477790 | `polymarket_headline.sql` | Polymarket |
| 8477772 | `polymarket_flows_daily.sql` | Polymarket |
| 8511800 | `freshness.sql` | Polymarket |

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

    curl -X DELETE -H "X-DUNE-API-KEY: $DUNE_API_KEY" \
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

**Settled and finalized are different populations and must not be divided by
each other.** `filled_orders_volume_usd` counts only swaps that settled;
`finalized_orders_count` counts settled plus failed. Around 7% of cross-chain
orders fail, so a ratio that mixes the two is wrong by that much. Every query
here now pairs like with like.

## What Dune's chart options actually do

Two findings, both established by rendering the result rather than by reading
documentation, which does not cover them:

**Stacked charts stack but do not normalise.** Neither `normalizeToPercentage`
nor `series.percentValues` has any effect, and `series.stacking: "percent"`
stacks without normalising. A share that should add to 100 has to be computed
in SQL.

**The percent tick format multiplies by a hundred.** Feed `tickFormat: "0%"` a
column already expressed in percent and the axis reads `10000%`. Emit fractions.

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
