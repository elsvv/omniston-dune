-- Omniston · swaps by hour of day, UTC
-- Cross-chain demand is not evenly spread across the day. Settlement time is
-- used rather than quote time because it is the moment the trade exists.
select
  lpad(cast(hour(from_unixtime(order_finalize_time)) as varchar), 2, '0') || ':00' as hour_utc,
  count(*) as swaps
from dune.elsvv.omniston_orders
where status = 'TRADE_STATUS_FULLY_FILLED' and src_chain_id != dst_chain_id
group by 1
order by 1
