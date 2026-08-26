-- Omniston · integrator league table
-- Each integrator picks its own fee, so take rate varies by an order of
-- magnitude between apps. Shown in basis points: 50 bps is half a percent.
select
  coalesce(substr(integrator_address, 1, 6) || '…' || substr(integrator_address, -4),
           'unattributed') as integrator,
  sum(filled_orders_volume_usd) as volume_usd,
  sum(finalized_orders_count)   as swaps,
  sum(filled_orders_volume_usd) / nullif(sum(finalized_orders_count), 0) as avg_swap_usd,
  sum(integrator_fees_usd)      as fees_earned_usd,
  10000.0 * sum(integrator_fees_usd) / nullif(sum(filled_orders_volume_usd), 0) as take_rate_bps
from dune.elsvv.omniston_daily_integrator
where src_chain_id != dst_chain_id
group by 1
having sum(integrator_fees_usd) > 0
order by fees_earned_usd desc
limit 12
