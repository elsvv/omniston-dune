-- Omniston · headline totals (single row, feeds the hero counter tiles)
-- Unique traders is counted from the order rows, not summed from the daily
-- cube: unique_trader_wallets_count is not additive across days.
-- Every figure on this row counts settled swaps. Volume was already filled-only
-- while the swap count was finalized -- settled plus failed -- so the two tiles
-- sat side by side describing different populations, and dividing one by the
-- other gave an average trade size for a trade that did not happen. Failures
-- are a reliability figure and have their own tile.
with cc as (
  select sum(filled_orders_volume_usd) as volume_usd,
         sum(case when status = 'TRADE_STATUS_FULLY_FILLED'
                  then finalized_orders_count else 0 end) as swaps
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
    count(distinct case when status = 'TRADE_STATUS_FULLY_FILLED'
                        then src_trader_address end) as traders,
    count(distinct case when status = 'TRADE_STATUS_FULLY_FILLED'
                        then resolver_id end) as resolvers,
    -- Settled swaps only. Failed orders have a median t_total of 316 seconds,
    -- and including them describes the wait for something that never arrived.
    approx_percentile(case when status = 'TRADE_STATUS_FULLY_FILLED'
                           then t_total end, 0.5) as median_seconds,
    100.0 * count_if(status = 'TRADE_STATUS_FULLY_FILLED') / count(*) as success_pct
  from dune.elsvv.omniston_orders
  where src_chain_id != dst_chain_id
)
-- Per-trader figures divide like with like: settled volume and settled swaps
-- over the traders who settled them. They say whether growth is more people or
-- the same people doing more, which the three totals above cannot.
select cc.volume_usd, cc.swaps, o.traders, ton.net_ton_usd,
       o.median_seconds, o.resolvers, o.success_pct,
       cc.volume_usd / o.traders as volume_per_trader_usd,
       cast(cc.swaps as double) / o.traders as swaps_per_trader
from cc, ton, o
