-- Omniston · swaps by hour of day, UTC
-- Cross-chain demand is not evenly spread across the day.
-- Bucketed by order_create_time. The service's own daily aggregates bucket by
-- creation, verified by comparing a month of daily counts both ways -- create
-- matched exactly, finalize was off by sixteen orders -- so anything charted
-- beside a cube-derived series has to agree with it.
select
  lpad(cast(hour(from_unixtime(order_create_time)) as varchar), 2, '0') || ':00' as hour_utc,
  count(*) as swaps
from dune.elsvv.omniston_orders
where status = 'TRADE_STATUS_FULLY_FILLED' and src_chain_id != dst_chain_id
group by 1
order by 1
