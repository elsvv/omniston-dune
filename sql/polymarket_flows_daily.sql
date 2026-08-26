-- Omniston · Polymarket deposits and withdrawals, daily
--
-- Polymarket settles in its own branded stablecoin, pUSD
-- (0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB on Polygon, 6 decimals).
-- That makes the token itself the identifier: a swap whose OUTPUT is pUSD is
-- money entering Polymarket, and one whose INPUT is pUSD is money leaving it.
-- No proxy-wallet or bytecode heuristics are involved.
--
-- Because pUSD is a 6-decimal dollar stablecoin, USD comes straight from the
-- raw units with no price feed, so these figures are exact rather than marked.
with flows as (
  select
    date_trunc('day', from_unixtime(order_create_time)) as day,
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
daily as (
  select
    day,
    coalesce(sum(case when direction =  1 then usd end), 0) as deposits_usd,
    coalesce(sum(case when direction = -1 then usd end), 0) as withdrawals_usd,
    sum(direction * usd)                                    as net_usd,
    count(distinct wallet)                                  as wallets
  from flows group by 1
)
select
  day,
  deposits_usd,
  -- plotted below the axis so deposits and withdrawals read as opposing flows
  -withdrawals_usd as withdrawals_usd,
  net_usd,
  wallets,
  avg(net_usd) over (order by day rows between 6 preceding and current row) as net_7d_avg
from daily
order by day
