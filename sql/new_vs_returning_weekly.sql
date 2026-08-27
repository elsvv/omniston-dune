-- Omniston · new against returning traders, weekly
-- A trader is "new" in the week they first appear and "returning" thereafter.
-- Retention is what separates a protocol people use from one they tried once.
with first_seen as (
  select src_trader_address as trader,
         min(date_trunc('week', from_unixtime(order_create_time))) as cohort_week
  from dune.elsvv.omniston_orders
  -- Settled swaps only, matching the headline trader count. A wallet whose
  -- only order failed has not traded, and counting it as a new trader who
  -- then never returned reports churn that never happened.
  where src_chain_id != dst_chain_id and status = 'TRADE_STATUS_FULLY_FILLED'
  group by 1
),
activity as (
  select distinct
         src_trader_address as trader,
         date_trunc('week', from_unixtime(order_create_time)) as active_week
  from dune.elsvv.omniston_orders
  -- Settled swaps only, matching the headline trader count. A wallet whose
  -- only order failed has not traded, and counting it as a new trader who
  -- then never returned reports churn that never happened.
  where src_chain_id != dst_chain_id and status = 'TRADE_STATUS_FULLY_FILLED'
),
weekly as (
  select
    a.active_week as week,
    count(distinct case when a.active_week = f.cohort_week then a.trader end) as new_traders,
    count(distinct case when a.active_week > f.cohort_week then a.trader end) as returning_traders
  from activity a
  join first_seen f on f.trader = a.trader
  group by 1
)
-- Cumulative traders is the running sum of new traders, not of the two columns
-- added together: a returning trader was already counted in the week they
-- first appeared, and summing both would count every repeat visit as a person.
select
  week,
  new_traders,
  returning_traders,
  sum(new_traders) over (order by week) as cumulative_traders
from weekly
order by week
