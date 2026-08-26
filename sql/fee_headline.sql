-- Omniston · fee headline
-- Two fees are charged on a swap. The integrator that brought the trade sets
-- and keeps its own cut; the protocol keeps a much smaller one. Splitting them
-- is the point: the ratio is the business model.
with t as (
  select
    sum(integrator_fees_usd)      as integrator_fees_usd,
    sum(protocol_fees_usd)        as protocol_fees_usd,
    sum(filled_orders_volume_usd) as volume_usd
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
),
i as (
  select count(distinct integrator_address) as integrators
  from dune.elsvv.omniston_daily_integrator
  where integrator_address is not null and filled_orders_volume_usd > 0
    and src_chain_id != dst_chain_id
)
select
  t.integrator_fees_usd,
  t.protocol_fees_usd,
  t.integrator_fees_usd + t.protocol_fees_usd as total_fees_usd,
  10000.0 * (t.integrator_fees_usd + t.protocol_fees_usd) / nullif(t.volume_usd, 0) as take_rate_bps,
  100.0 * t.integrator_fees_usd
        / nullif(t.integrator_fees_usd + t.protocol_fees_usd, 0) as integrator_share_pct,
  i.integrators
from t, i
