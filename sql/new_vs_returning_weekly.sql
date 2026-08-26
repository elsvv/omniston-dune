-- Omniston · new against returning traders, weekly
-- A trader is "new" in the week they first appear and "returning" thereafter.
-- Retention is what separates a protocol people use from one they tried once.
with first_seen as (
  select src_trader_address as trader,
         min(date_trunc('week', from_unixtime(order_create_time))) as cohort_week
  from dune.elsvv.omniston_orders
  where src_chain_id != dst_chain_id
  group by 1
),
activity as (
  select distinct
         src_trader_address as trader,
         date_trunc('week', from_unixtime(order_create_time)) as active_week
  from dune.elsvv.omniston_orders
  where src_chain_id != dst_chain_id
)
select
  a.active_week as week,
  count(distinct case when a.active_week = f.cohort_week then a.trader end) as new_traders,
  count(distinct case when a.active_week > f.cohort_week then a.trader end) as returning_traders
from activity a
join first_seen f on f.trader = a.trader
group by 1
order by 1
