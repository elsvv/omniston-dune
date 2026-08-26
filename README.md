# omniston-dune

Publishes STON.fi Omniston swap history into Dune as seven queryable tables.

## Why a pipeline at all

Dune executes SQL only against data already inside Dune; it cannot call an
external API. Omniston history lives behind a public JSON-RPC service, so the
data has to be pushed in. Pressing "Run" on a Dune query re-reads the last
snapshot that was uploaded — it does not fetch anything new.

## Setup

    python -m pip install -e ".[dev]"
    cp .env.example .env    # fill in DUNE_API_KEY and DUNE_NAMESPACE

The API key needs `Read/Write` scope (dune.com → Settings → API).
`.env` is read automatically on a local run; in CI the same variables come
from repository secrets instead, so no `.env` file is needed there.

## Running

    python -m omniston_dune --dry-run                    # fetch only, report counts
    python -m omniston_dune                              # full refresh
    python -m omniston_dune --query-ids 8309209,8309210  # and refresh dashboards

Every run is a full refresh of all history: each table is cleared and refilled.
That is deliberate. Some orders finalize days after they were created, so a
given day's success rate keeps changing for a while; recomputing everything
makes those corrections self-healing.

## Tables

| Table | Grain |
| --- | --- |
| `omniston_daily_total` | day |
| `omniston_daily_chainpair` | day × src chain × dst chain × status |
| `omniston_daily_resolver` | day × resolver × status |
| `omniston_daily_input_asset` | day × input asset |
| `omniston_daily_output_asset` | day × output asset (excludes input volume) |
| `omniston_daily_integrator` | day × integrator |
| `omniston_orders` | one row per finalized order |

They land at `dune.<your-namespace>.<table>`.

Two things to know before querying them:

`unique_trader_wallets_count` is **not additive**. Summing it across chain pairs
counts one trader once per route. Use `omniston_daily_total` for headline user
counts, and add a cube if you need uniques at another grain.

Both USD metrics are **input-side**. `omniston_daily_output_asset` excludes only
those two volume metrics -- volume attributed to a bought asset does not exist
in the source data. Fees still belong at this grain: the protocol collects both
`protocol_fees_usd` and `integrator_fees_usd` in the output asset, so this
table carries fees and counts, just not the input-side volume columns.

## Cost

Measured on the first full run, 2026-08-26, against a free-tier account:

| | |
| --- | --- |
| Credits | **15.12** of 2,500/month, covering one full run plus a few ad-hoc queries |
| Storage | **59.1 MB** of 95.4 MB |
| Wall clock | **4m 38s**, most of it the deliberate pacing between writes |
| Rows | 41,457 orders and 6,539 cube rows across seven tables |

Credits are not the constraint. **Storage is.** The orders table is about 1.4 KB
per row across its 38 columns, and every run republishes the entire history, so
the footprint grows with the history itself — roughly 0.4 MB a day at the
current rate of ~300 orders/day. That leaves on the order of three months of
headroom before the free tier's cap.

When that approaches, the cheapest fix is to stop publishing the full order
history: keep the cubes for all time and trim `omniston_orders` to a rolling
window, since the latency and fill-quality charts it feeds only ever look at
recent behaviour. Dropping unused columns would buy less, and raising the tier
buys time rather than solving it.
