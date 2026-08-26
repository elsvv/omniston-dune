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
)
xc as (
  -- The denominator is cross-chain swaps, not all Omniston swaps: this
  -- dashboard is about the cross-chain business.
  select sum(finalized_orders_count) as swaps
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
  100.0 * count(*) / nullif((select swaps from xc), 0)          as share_of_swaps_pct
from flows
