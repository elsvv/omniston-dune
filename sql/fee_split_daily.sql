-- Omniston · fees earned per day, split between integrator and protocol
select
  day,
  sum(integrator_fees_usd) as integrator_fees_usd,
  sum(protocol_fees_usd)   as protocol_fees_usd
from dune.elsvv.omniston_daily_chainpair
where src_chain_id != dst_chain_id
group by 1
order by 1
