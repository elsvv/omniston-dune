-- Omniston · integrator league table
-- Which apps send cross-chain flow, and what they charge for it. Ordered by
-- volume rather than by fee, because on cross-chain the fees are still small
-- enough that ordering by them would rank noise. Take rate is in basis points:
-- 100 bps is one percent, and each app picks its own.
select
  coalesce(substr(integrator_address, 1, 6) || '…' || substr(integrator_address, -4),
           'unattributed') as integrator,
  sum(filled_orders_volume_usd) as volume_usd,
  sum(finalized_orders_count)   as swaps,
  sum(filled_orders_volume_usd) / nullif(sum(finalized_orders_count), 0) as avg_swap_usd,
  sum(integrator_fees_usd)      as fees_earned_usd,
  10000.0 * sum(integrator_fees_usd) / nullif(sum(filled_orders_volume_usd), 0) as take_rate_bps
from dune.elsvv.omniston_daily_integrator
where src_chain_id != dst_chain_id and integrator_address is not null
group by 1
having sum(filled_orders_volume_usd) > 0
order by volume_usd desc
limit 12
