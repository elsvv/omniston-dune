-- Omniston · chain-to-chain flow
-- Source and destination labels are suffixed differently so a chain that both
-- sends and receives does not collapse into a single node and create a cycle.
select
  src_chain_id || ' (from)' as source,
  dst_chain_id || ' (to)'   as target,
  sum(filled_orders_volume_usd) as value
from dune.elsvv.omniston_daily_chainpair
where src_chain_id != dst_chain_id
group by 1, 2
having sum(filled_orders_volume_usd) >= 100
order by value desc
