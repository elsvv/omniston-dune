-- Omniston · net capital flow by chain
-- Positive means the chain is a net importer of value across Omniston.
-- Only cross-chain legs count; an intrachain swap moves nothing between chains.
with legs as (
  select dst_chain_id as chain, filled_orders_volume_usd as usd, 'in'  as side
  from dune.elsvv.omniston_daily_chainpair where src_chain_id != dst_chain_id
  union all
  select src_chain_id, filled_orders_volume_usd, 'out'
  from dune.elsvv.omniston_daily_chainpair where src_chain_id != dst_chain_id
)
select
  chain,
  sum(case when side = 'in'  then usd else 0 end) as inflow_usd,
 -sum(case when side = 'out' then usd else 0 end) as outflow_usd,
  sum(case when side = 'in'  then usd else -usd end) as net_usd
from legs
group by 1
having sum(case when side = 'in' then usd else usd end) > 0
order by net_usd desc
