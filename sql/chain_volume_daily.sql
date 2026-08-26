-- Omniston · daily volume by destination chain
-- Which chains are actually growing. Destination rather than source, because
-- that is where the trade lands. Top six by lifetime volume keep their own
-- band; the rest are pooled so the chart stays readable.
with d as (
  select day, dst_chain_id as chain, sum(filled_orders_volume_usd) as usd
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
  group by 1, 2
),
top as (
  select chain from d group by 1 order by sum(usd) desc limit 6
)
select
  d.day,
  case when d.chain in (select chain from top) then d.chain else 'other' end as chain,
  sum(d.usd) as volume_usd
from d
group by 1, 2
order by 1
