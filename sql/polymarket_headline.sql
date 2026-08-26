-- Omniston · Polymarket headline totals (single row, feeds the counter tiles)
-- pUSD is Polymarket's own 6-decimal dollar stablecoin, so these are exact.
with flows as (
  select
    case when output_asset_address = '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'
         then 1 else -1 end as direction,
    case when output_asset_address = '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'
         then cast(actual_output_units as double)
         else cast(actual_input_units  as double) end / 1e6 as usd,
    case when output_asset_address = '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'
         then dst_trader_address else src_trader_address end as wallet
  from dune.elsvv.omniston_orders
  where status = 'TRADE_STATUS_FULLY_FILLED' and src_chain_id != dst_chain_id
    and (output_asset_address = '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'
      or input_asset_address  = '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB')
),
xc as (
  -- The denominator is cross-chain swaps, not all Omniston swaps: this
  -- dashboard is about the cross-chain business. Settled swaps only, matching
  -- the numerator -- `flows` already drops anything that failed, and dividing
  -- settled swaps by finalized ones understated the share by six points.
  --
  -- Volume here is input-side while a deposit is measured in the pUSD it
  -- delivered, so the volume share carries the spread between the two sides.
  -- That is a fraction of a percent and does not move the figure.
  select
    sum(case when status = 'TRADE_STATUS_FULLY_FILLED'
             then finalized_orders_count else 0 end) as swaps,
    sum(filled_orders_volume_usd)                    as volume_usd
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
)
select
  sum(case when direction =  1 then usd end)                    as deposits_usd,
  sum(case when direction = -1 then usd end)                    as withdrawals_usd,
  sum(direction * usd)                                          as net_usd,
  count(distinct wallet)                                        as wallets,
  count(*)                                                      as transfers,
  approx_percentile(case when direction = 1 then usd end, 0.5)  as median_deposit_usd,
  100.0 * count(*) / nullif((select swaps from xc), 0)          as share_of_swaps_pct,
  100.0 * sum(usd) / nullif((select volume_usd from xc), 0)     as share_of_volume_pct
from flows
