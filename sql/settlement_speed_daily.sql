-- Omniston · settlement speed over time
-- Percentiles, not averages: the tail is long enough that a mean would
-- describe a swap almost nobody gets. Days with fewer than twenty settled
-- swaps are dropped, since a percentile over a handful of orders is noise.
--
-- Bucketed by order_create_time. The service's own daily aggregates bucket by
-- creation, verified by comparing a month of daily counts both ways -- create
-- matched exactly, finalize was off by sixteen orders -- so anything charted
-- beside a cube-derived series has to agree with it.
select
  date_trunc('day', from_unixtime(order_create_time)) as day,
  approx_percentile(t_total, 0.5) as p50_seconds,
  approx_percentile(t_total, 0.9) as p90_seconds
from dune.elsvv.omniston_orders
where status = 'TRADE_STATUS_FULLY_FILLED' and t_total > 0
  and src_chain_id != dst_chain_id
group by 1
having count(*) >= 20
order by 1
