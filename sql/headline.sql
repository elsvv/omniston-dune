-- Omniston · headline totals (single row, feeds the hero counter tiles)
-- Unique traders is counted from the order rows, not summed from the daily
-- cube: unique_trader_wallets_count is not additive across days.
with cc as (
  select sum(filled_orders_volume_usd) as volume_usd,
         sum(finalized_orders_count)   as swaps
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
),
ton as (
  select
    sum(case when dst_chain_id = 'ton' and src_chain_id != 'ton' then filled_orders_volume_usd else 0 end)
  - sum(case when src_chain_id = 'ton' and dst_chain_id != 'ton' then filled_orders_volume_usd else 0 end) as net_ton_usd
  from dune.elsvv.omniston_daily_chainpair
),
o as (
  select
    count(distinct src_trader_address) as traders,
    count(distinct resolver_id)        as resolvers,
    approx_percentile(t_total, 0.5)    as median_seconds,
    100.0 * count_if(status = 'TRADE_STATUS_FULLY_FILLED') / count(*) as success_pct
  from dune.elsvv.omniston_orders
  where src_chain_id != dst_chain_id
)
select cc.volume_usd, cc.swaps, o.traders, ton.net_ton_usd,
       o.median_seconds, o.resolvers, o.success_pct
from cc, ton, o
