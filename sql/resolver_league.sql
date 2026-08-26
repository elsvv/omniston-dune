-- Omniston · resolver league table
-- The interesting column is the average trade size: the resolvers specialise,
-- one taking large trades and another absorbing high-count retail flow.
with vol as (
  select resolver_id,
         sum(filled_orders_volume_usd) as volume_usd,
         sum(case when status = 'TRADE_STATUS_FULLY_FILLED' then finalized_orders_count else 0 end) as filled,
         sum(finalized_orders_count) as finalized
  from dune.elsvv.omniston_daily_resolver
  where resolver_id is not null and src_chain_id != dst_chain_id
  group by 1
),
lat as (
  select resolver_id,
         approx_percentile(t_settle, 0.5) as median_settle_seconds,
         count(distinct src_trader_address) as traders
  from dune.elsvv.omniston_orders
  where status = 'TRADE_STATUS_FULLY_FILLED' and resolver_id is not null
    and src_chain_id != dst_chain_id
  group by 1
)
select
  substr(vol.resolver_id, 1, 10) || '…' as resolver,
  vol.volume_usd,
  vol.finalized as orders,
  vol.volume_usd / nullif(vol.filled, 0) as avg_trade_usd,
  100.0 * vol.filled / nullif(vol.finalized, 0) as success_pct,
  lat.median_settle_seconds,
  lat.traders
from vol left join lat on lat.resolver_id = vol.resolver_id
order by vol.volume_usd desc
