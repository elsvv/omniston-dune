-- Omniston · Daily cross-chain volume, with a 7-day moving average.
-- Cross-chain is src_chain_id != dst_chain_id. That is deliberately not the
-- same as "everything except TON-to-TON": intrachain swaps also occur on
-- base, bnb, ethereum, polygon, arbitrum and avalanche, and counting those
-- as cross-chain would overstate the headline.
--
-- The 7-day average is over the last seven rows, not the last seven calendar
-- days. A day with no cross-chain volume produces no row, so in the first
-- sparse weeks the window reaches further back than a week. From June onward
-- every day has volume and the two are the same thing.
with daily as (
  select
    day,
    sum(filled_orders_volume_usd) as volume_usd,
    sum(case when status = 'TRADE_STATUS_FULLY_FILLED'
             then finalized_orders_count else 0 end) as swaps
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
  group by 1
)
select
  day,
  volume_usd,
  swaps,
  avg(volume_usd) over (order by day rows between 6 preceding and current row) as volume_7d_avg,
  -- Cumulative volume answers a question the daily bars cannot: is the total
  -- still bending upward, or has growth gone flat while the daily noise hides
  -- it? It belongs on its own axis -- see the note in build.py's chart().
  sum(volume_usd) over (order by day) as cumulative_usd,
  sum(swaps) over (order by day) as cumulative_swaps
from daily
order by day
