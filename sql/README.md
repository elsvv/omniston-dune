# Dashboard SQL

Each file is the source of one saved Dune query. Dune is the deployment target,
not the source of truth — edit here, then push with the Dune CLI.

| Query ID | File | Dashboard section |
| --- | --- | --- |
| 8477613 | `cross_chain_volume_daily.sql` | Growth |
| 8477772 | `polymarket_flows_daily.sql` | Polymarket |
| 8477790 | `polymarket_headline.sql` | Polymarket counter tiles |

Dashboard: https://dune.com/elsvv/omniston-cross-chain-elsvv

## A note on writing to Dune

The free tier allows 15 write requests per minute and the CLI does not pace
itself, so creating several queries or visualizations back to back returns
`429 Too many requests`. Space writes out — the ingest pipeline uses 6 seconds
between writes for the same reason.

## Identifying Polymarket

Polymarket settles in its own branded stablecoin, `pUSD`
(`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` on Polygon, 6 decimals), confirmed
against Dune's `tokens.erc20`. The token is therefore the identifier: pUSD as
the output asset is a deposit, pUSD as the input asset is a withdrawal. No proxy
wallet or bytecode heuristic is needed, and since pUSD is a dollar stablecoin
the USD figures come straight from the raw units rather than a price feed.
