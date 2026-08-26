# Omniston Cross-Chain Analytics Dashboard on Dune — Design

Date: 2026-08-26
Status: approved for planning

## 1. Goal

Build a public Dune dashboard for STON.fi's Omniston cross-chain swap protocol that
shows genuine insight, refreshes itself daily, and runs entirely on a free Dune account.

The dashboard must reproduce the protocol's own headline numbers exactly, so that
figures quoted by the team and figures on the dashboard never disagree.

## 2. Established facts

Everything in this section was verified by live API calls or by reading primary
sources on 2026-08-26. Items marked ASSUMED are not verified and must be
re-checked during implementation.

### 2.1 Omniston History API

Public JSON-RPC, no authentication, CORS `*`:
`https://omni-history.ston.fi/json-rpc`
(also gRPC `omni-history-grpc.ston.fi:443` and WS `wss://omni-history-ws.ston.fi`)

Three methods:
- `stonfi.omni.history.v1.FinalizedOrdersRpc.List`
- `stonfi.omni.history.v1.FinalizedOrdersRpc.GetByQuoteId`
- `stonfi.omni.history.v1.AggregatesRpc.FinalizedOrderAggregates`

Protobuf source of truth:
`https://github.com/ston-fi/stonfi-proto/tree/main/proto/stonfi/omni/history/v1`

**Aggregates** — dimensions: `src_chain_id`, `dst_chain_id`, `input_asset`,
`output_asset`, `src_trader_address`, `dst_trader_address`, `src_resolver_address`,
`dst_resolver_address`, `integrator_address`, `resolver_id`, `status`.
Time grouping: `TIME_GROUPING_DAY` | `TIME_GROUPING_HOUR`.
Metrics: `finalized_orders_volume_usd`, `filled_orders_volume_usd`,
`protocol_fees_usd`, `integrator_fees_usd`, `finalized_orders_count`,
`unique_trader_wallets_count`.
Hard limit: `time_range` window must not exceed 31 days.

**List** — ascending by `lt`, `limit` 1..1000 (default 1000), cursor `prev_lt`,
`has_next_page` flag.

Per-order fields include the four timestamps `quote_request_time`, `quote_time`,
`order_create_time`, `order_finalize_time`; `quote_input_units` /
`quote_output_units` vs `actual_input_units` / `actual_output_units`;
`protocol_fee_pips` / `integrator_fee_pips` and their `actual_*_units`;
`src`/`dst` trader and resolver addresses; `resolver_id`; `integrator_address`.

### 2.2 API behaviours that shape the design

1. **`lt` is a nanosecond timestamp anchored to finalization.** Verified by seeking
   `prev_lt = int(unix_seconds * 1e9)` to 2026-07-15 and landing on orders finalized
   at 00:01:49 that day. Consequence: no cursor state is needed; any date is directly
   addressable.

2. **`lt` orders by finalize time; `time_range` filters on create time.** These are
   different clocks. A small tail of orders finalizes long after creation, so
   aggregates for a past day keep changing for several days. Every late finalizer
   observed was `FAILED`, so filled volume is stable but **success rate for a given
   day is not final for about a week**. In a 1000-order sample starting 2026-08-01,
   zero orders finalized more than one day after creation — the tail is roughly 0.3%
   and was not reproduced locally, so treat the exact width as ASSUMED. The daily full
   refresh in section 3.3 makes the exact width irrelevant, which is the main reason
   that design was chosen.

3. **Only two statuses exist in all history.** Verified month by month across
   2026-04 to 2026-08: `FULLY_FILLED` and `FAILED` only. No `PARTIALLY_FILLED`,
   `CANCELLED`, or `IN_PROGRESS` has ever occurred. Settlement is all-or-nothing.

4. **`FAILED` means the trader got nothing and paid nothing** — `actual_input_units`
   and `actual_output_units` are both exactly `"0"`. But failed orders are still
   counted in `finalized_orders_volume_usd`. Therefore the headline volume metric
   must always be `filled_orders_volume_usd`.

5. **Both USD metrics are input-side.** `filled_orders_volume_usd` is the USD value
   of `actual_input_units`; `finalized_orders_volume_usd` is the USD value of
   `quote_input_units`. Grouping by `output_asset` is permitted, but the USD figure
   in those rows is still input-side. Volume attributed to a bought asset is not
   available and must not be presented.

6. **`quote_output_units` is missing on roughly a quarter of orders** (25.2% in a
   1000-order sample over 7 days). Every missing row came from a single resolver
   (`EQDE_Tw...`) on TON to EVM routes. Any quote-vs-delivered metric is therefore
   biased by resolver and route, and must state its coverage.

7. **`quote_output_units` is gross, `actual_output_units` is net of fees.** The
   median gap is exactly the sum of `protocol_fee_pips` and `integrator_fee_pips`.
   There is no slippage or price-impact field in the API. A "zero slippage" claim is
   only defensible as "delivered equals quote minus stated fees".

8. **Two filters hang forever** — `resolver_id_in_list` and
   `integrator_address_in_list` return no response and no error on both methods.
   The same fields work correctly as aggregate dimensions. Never use them as
   filters; always set a client timeout.

9. **Empty results omit the `rows` key entirely** — the response is
   `{"jsonrpc":"2.0","id":N,"result":{}}`. Verified repeatedly.

10. **Rows where all requested metrics are zero are dropped.** A chain pair with
    only failed orders vanishes from a volume-only query. Always request
    `finalized_orders_count` alongside any volume metric.

11. **`unique_trader_wallets_count` is not additive.** It must be requested at the
    exact granularity it will be displayed at.

12. **`resolver_name` is never populated.** Resolver display names require a
    hand-maintained lookup. There are currently four resolver IDs.

13. **No transaction hashes anywhere.** Explorer deep links are impossible.

14. **Cloudflare rejects requests with a default `urllib` User-Agent** (403,
    code 1010). Set an explicit UA.

15. **No rate limiting observed.** JSON-RPC batching (array in, array out) works.

### 2.3 Chains

Present in live data: `arbitrum`, `avalanche`, `base`, `bnb`, `ethereum`, `polygon`,
`robinhood`, `ton`, `tron`, and `xlayer`.

`xlayer` does **not** appear in the published protobuf `AssetId` oneof. The chain
list must be derived from data at runtime and never hardcoded, or new chains will
silently disappear from the dashboard.

Non-TON intrachain swaps exist: base to base, bnb to bnb, ethereum to ethereum,
polygon to polygon in 2026-06; arbitrum, avalanche, polygon in 2026-07. 54 orders
total, about $411. Cross-chain must therefore be defined as
`src_chain_id != dst_chain_id`, not as "excluding TON to TON".

### 2.4 Scale of the data

- History begins about 2026-04.
- 40,859 orders across 2026-04 to 2026-08 by monthly status counts.
- Last 30 days: $5.06M finalized, $5.00M filled, 7,809 orders, 2,735 unique wallets,
  7,493 filled against 316 failed (95.9%).
- Cross-chain only: $126,583 on 2026-08-24 and $149,371 on 2026-08-25 — matching the
  figures circulated internally, which confirms the cross-chain definition.
- Four resolvers, with sharply different average trade sizes ($63, $236, $1,367,
  $2,192) — they specialize by trade size.
- End-to-end latency p50 24s, p90 69s, p99 312s.
- Net 30-day flow: TON +$435k, BNB -$442k.

All of this is a few megabytes at most in any tabular encoding.

### 2.5 Dune platform

Free plan: 2,500 credits per month, 100 MB storage, writes cost 3 credits per GB
with a 1-credit minimum per operation, low-limit endpoints capped at 15 requests
per minute. All uploaded data is public; private uploads are Enterprise only.

Available on free: CSV upload (UI and API), programmatic table create and
incremental insert, query execution via API.
Not available on free: creating or updating queries via API (Plus and above).
Queries and dashboard layout are therefore authored by hand in the web UI.

Endpoints are `/v1/uploads/*`; the old `/v1/table/*` paths were scheduled for
removal on 2026-03-01.

Visualizations: bar, area, line, scatter, pie, mixed, counter, table. **No Sankey,
chord, map, or heatmap primitive.** Bars and lines can be mixed in one chart as long
as the base is not a pie. Normalize-to-percentage is a checkbox.

Dune indexes TON natively: `ton.transactions`, `ton.messages`, `ton.dex_trades`,
`ton.dex_pools`, `ton.jetton_events`, `ton.jetton_metadata`, `ton.prices_daily`,
`ton.balances_history`, `ton.accounts`, `ton.blocks`. `ton.dex_trades` covers
STON.fi and DeDust.

### 2.6 What already exists

`dune.com/stonfi_protocol/omniston-cross-chain` (dashboard 218196, created
2026-08-13) reads from the uploaded dataset
`dune.stonfi_protocol.dataset_omniston_daily_by_route`. No native table is used.
It has two column charts, queries `8309209` and `8309210`.

Three defects in it, to be raised with the team rather than silently fixed:
- Query `8309209` computes an order-count column that is not mapped to any axis, so
  the "& orders" in its title does not render.
- It aliases `finalized_orders_count` as `filled_orders_count`; finalized includes
  the 2,936 failed orders.
- Its cross-chain filter is `not (src_chain_id = 'ton' and dst_chain_id = 'ton')`,
  which misclassifies the 54 non-TON intrachain orders as cross-chain.

A separate prototype, `omni-example` by `@smehnov_team_6076`, has 9 queries over 4
uploaded tables. A colleague is already working in this area; coordinate before
publishing.

## 3. Architecture

A small Python project pushes data into the user's own Dune namespace on a daily
cron. Queries and dashboard layout are authored by hand in the Dune UI, with the SQL
mirrored into the repository so it is reviewable and recoverable.

    Omniston History API  ->  ingest (Python)  ->  Dune uploads API  ->  SQL  ->  dashboard
                                                          |
                                   Dune native ton.* tables joined at query time

The pipeline mirrors what STON.fi already does, so the work can be handed over or
repointed at the team's own dataset without redesign.

### 3.1 Components

- `omniston/client.py` — JSON-RPC client. Explicit User-Agent, per-request timeout,
  retry with backoff, `lt`-based seeking, `.get("rows", [])` everywhere.
- `omniston/cubes.py` — aggregate pulls, one function per cube.
- `omniston/orders.py` — paginated order pull with derived latency intervals.
- `dune/uploads.py` — create table, insert rows, clear table against `/v1/uploads/*`.
- `dune/refresh.py` — trigger query executions so the dashboard shows fresh results.
- `main.py` — `backfill` and `daily` entry points.
- `sql/` — one file per Dune query, kept in sync by hand with a query ID in a header
  comment.
- `.github/workflows/daily.yml` — cron, holding `DUNE_API_KEY` as a repo secret.

### 3.2 Data model

Two independent groups. They are never joined or plotted on a shared axis, because
counts derived from cubes and from raw orders can disagree at day boundaries.

Group A, cubes from the Aggregates API, all keyed by UTC day:

- `omniston_daily_total` — the only source for unique wallet counts at the top level.
- `omniston_daily_chainpair` — day, src_chain_id, dst_chain_id, status, volumes,
  fees, order count.
- `omniston_daily_resolver` — day, resolver_id, status, volumes, fees, order count.
- `omniston_daily_input_asset` — day, chain, asset kind, asset address, volume, count.
- `omniston_daily_output_asset` — day, chain, asset kind, asset address, **count
  only**; no volume column, because volume here would be input-side and misleading.
- `omniston_daily_integrator` — day, integrator chain and address, volume,
  integrator fees, count.

Any chart needing unique wallets at a non-total granularity requires its own cube at
exactly that granularity. Adding such a chart means adding a cube.

Group B, raw orders:

- `omniston_orders` — one row per finalized order, all API fields flattened, plus
  four precomputed intervals: `t_quote` (request to quote), `t_decide` (quote to
  create), `t_settle` (create to finalize), `t_total` (request to finalize).

### 3.3 Pipeline behaviour

`backfill` walks the history in 31-day windows for cubes, and seeks by `lt` for
orders, writing everything from about 2026-04 to now.

`daily` performs a **full refresh**: it re-pulls the entire history and, for each
table, clears it and re-inserts everything. Dune's `clear` operation empties a whole
table and cannot delete a date range, so a partial replace is not expressible against
this API; a full refresh is the only correct way to apply retroactive corrections.

This is affordable precisely because the dataset is small — about 41,000 order rows
and a few thousand cube rows, a few megabytes in total. A complete order pull takes
roughly a minute, and cube pulls are 31-day windows over five months.

Full refresh also removes the late-finalization problem entirely. Because every day's
figures are recomputed from scratch, corrections to orders that finalized after their
creation date are picked up automatically, and the exact width of that tail — the one
number in section 2.2 that remains ASSUMED — stops mattering.

The tradeoff is that this design does not scale indefinitely. At roughly 41,000 rows
growing by about 250 a day, it has years of headroom against the 100 MB storage cap.
If the orders table ever approaches tens of megabytes, switch to an append-only
historical table plus a small clear-and-reinsert recent table, unioned in SQL.

After the data step, `daily` executes each dashboard query so cached results update.
Without this the dashboard shows stale figures even though the data landed.

Estimated cost: 7 tables cleared and re-inserted, so about 14 write operations at a
1-credit minimum each, plus about 18 query executions, per day. Against 2,500 credits
per month this leaves substantial headroom, but actual consumption must be measured
during the first week rather than trusted from this estimate.

## 4. Dashboard

### 4.1 Above the fold

Six counter tiles and nothing else, each showing 24h / 7d / all-time: cross-chain
volume, swaps, unique traders, net TON inflow, median settlement time, active
resolvers.

Below them, global parameters — granularity, period, source chain, destination
chain, minimum trade size — with an explicit note that they drive every chart on the
page.

### 4.2 Sections, in order

1. **Growth** — daily cross-chain and intrachain volume as stacked columns with a
   7-day moving average overlaid as a line; daily orders; new against returning
   wallets. Kept deliberately short.

2. **Flows and routes** — net flow per chain as a diverging bar, the chart that
   carries the TON narrative; a source-by-destination matrix as a table with a colour
   scale, built twice, once for volume and once for success rate; top chain pairs by
   volume and separately by count, since they rank differently; Omniston's share of
   total TON DEX volume, joining `ton.dex_trades`.

3. **Execution quality** — latency percentiles p50/p90/p99 on a log axis, broken
   down by trade size bucket; settlement time by chain pair; success rate over time
   with failed volume; quote versus delivered, annotated with its coverage caveat.

4. **Resolver market** — share of volume over time as a 100% stacked area; average
   trade size per resolver, showing specialization; a league table of orders, volume
   share, success rate and median settlement time per resolver. Resolvers are shown
   by name from the hand-maintained lookup, never as raw addresses.

5. **Users** — trade size distribution, mean against median, top-10 concentration,
   retention as curves with the cohort table beneath.

6. **Methodology** — definitions of every metric, the cross-chain definition, known
   coverage gaps, and the refresh schedule.

Roughly 18 charts. More becomes a wall nobody reads.

### 4.3 Presentation rules

Every noisy daily column chart carries a 7-day moving average line. Percentile axes
are logarithmic. USD axes use a compact tick format. Every chart has a one-line
definition beneath it. No pie chart may exceed 8 slices; anything longer becomes a
ranked table.

## 5. Explicitly out of scope

- **Quote-competition metrics** — win rate, quotes per request, price improvement
  against the second-best quote, counterfactual solver value. The History API
  contains only finalized orders; losing quotes do not exist in it. Presenting a
  market-share pie under a "quote competition" heading would misrepresent what the
  data supports.
- **Volume attributed to bought assets** — both USD metrics are input-side.
- **Explorer deep links** — no transaction hashes in the API.
- **EVM token symbols** — TON symbols come from `ton.jetton_metadata`; EVM symbol
  resolution is deferred until the chart set has settled.
- **On-chain reconciliation of the EVM side** — stitching `quote_id` to transactions
  across eight chains is a separate project that does not pay for itself here.
- **Seasonality analysis** — five months of history cannot support it.

## 6. Risks

- Full refresh means a failed run leaves the previous day's data in place rather than
  corrupting it, but a run that clears and then fails mid-insert leaves a table empty.
  Insert before clearing where the API allows it, or verify row counts after each run
  and alert on an unexpected drop.
- Credit consumption is estimated, not measured. Monitor during week one.
- The quote-coverage gap is concentrated in one resolver; if that resolver's share
  moves, the quote-quality chart shifts for reasons unrelated to execution quality.
- A new chain appearing in the API will pass through correctly only if nothing
  hardcodes the chain list. This must be checked in review.
- Clear-and-reinsert is safe at current volume and unsafe at large volume. Revisit if
  the orders table grows substantially.

## 7. Verification

- The pipeline's cross-chain daily volume must reproduce $126,583 for 2026-08-24 and
  $149,371 for 2026-08-25.
- Total orders by month must match 82/3, 9809/867, 9904/839, 11695/958, 6433/269
  filled/failed for 2026-04 through 2026-08.
- Cube totals and raw-order counts are compared and their divergence recorded; they
  are expected to differ slightly at day boundaries and must never share an axis.
- A synthetic empty window must be handled without raising, exercising the missing
  `rows` key.
- The hanging filters must not appear anywhere in the codebase.
