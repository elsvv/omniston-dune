-- Omniston · weekly trader retention curves
--
-- A trader belongs to the week they first swapped. Each line follows one such
-- cohort forward: what share of it was still swapping one week later, two
-- weeks later, and so on. This is the question the new-against-returning bars
-- raise but cannot answer.
--
-- Drawn as lines rather than as Dune's cohort grid: the cohort visualisation
-- renders blank for every option shape tried, and a chart that works beats a
-- chart that is the right idea.
--
-- Cohorts under twenty traders are dropped. The first weeks had six, and a
-- retention curve over six people is a curve over six people.
with first_week as (
  select src_trader_address as trader,
         min(date_trunc('week', from_unixtime(order_create_time))) as cohort_week
  from dune.elsvv.omniston_orders
  -- Settled swaps only, matching the headline trader count.
  where src_chain_id != dst_chain_id and src_trader_address is not null
    and status = 'TRADE_STATUS_FULLY_FILLED'
  group by 1
),
activity as (
  select distinct
         src_trader_address as trader,
         date_trunc('week', from_unixtime(order_create_time)) as active_week
  from dune.elsvv.omniston_orders
  -- Settled swaps only, matching the headline trader count.
  where src_chain_id != dst_chain_id and src_trader_address is not null
    and status = 'TRADE_STATUS_FULLY_FILLED'
),
sizes as (
  select cohort_week, count(*) as cohort_size from first_week group by 1
)
select
  date_diff('week', f.cohort_week, a.active_week) as weeks_after,
  'week of ' || date_format(f.cohort_week, '%b %e') as cohort,
  -- A fraction, not a percentage: Dune's percent tick format multiplies by a
  -- hundred itself, so handing it percentages labels the axis 10000%.
  cast(count(distinct a.trader) as double) / max(s.cohort_size) as retained
from activity a
join first_week f on f.trader = a.trader
join sizes     s on s.cohort_week = f.cohort_week
where s.cohort_size >= 20
group by 1, 2, f.cohort_week
order by f.cohort_week, 1
