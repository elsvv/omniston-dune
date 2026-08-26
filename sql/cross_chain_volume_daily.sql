-- Omniston · Daily cross-chain volume, with a 7-day moving average.
-- Cross-chain is src_chain_id != dst_chain_id. That is deliberately not the
-- same as "everything except TON-to-TON": intrachain swaps also occur on
-- base, bnb, ethereum, polygon, arbitrum and avalanche, and counting those
-- as cross-chain would overstate the headline.
with daily as (
  select
    day,
    sum(filled_orders_volume_usd) as volume_usd,
    sum(finalized_orders_count)   as orders
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
  group by 1
)
select
  day,
  volume_usd,
  orders,
  avg(volume_usd) over (order by day rows between 6 preceding and current row) as volume_7d_avg
from daily
order by day
