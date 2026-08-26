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
| `omniston_daily_output_asset` | day × output asset (counts only) |
| `omniston_daily_integrator` | day × integrator |
| `omniston_orders` | one row per finalized order |

They land at `dune.<your-namespace>.<table>`.

Two things to know before querying them:

`unique_trader_wallets_count` is **not additive**. Summing it across chain pairs
counts one trader once per route. Use `omniston_daily_total` for headline user
counts, and add a cube if you need uniques at another grain.

Both USD metrics are **input-side**. `omniston_daily_output_asset` therefore
carries counts and no volume; volume attributed to a bought asset does not exist
in the source data.

## Cost

Roughly 14 write operations and a handful of query executions per day, against
2,500 free-tier credits per month. The Dune client paces every write request
4.5 seconds apart to stay under the free tier's 15-requests-per-minute write
limit, and retries a 429 up to twice — so a full run takes several minutes,
not seconds; that is expected, not a hang. Check consumption at dune.com →
Settings → Billing during the first week and replace this estimate with a
measured figure rather than trusting it.
