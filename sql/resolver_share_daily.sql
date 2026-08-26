-- Omniston · resolver share of filled volume, daily
-- The API never populates resolver_name, so resolvers are shown by a short
-- prefix of their ID. Stable, and honest about what we actually know.
select
  day,
  substr(resolver_id, 1, 8) || '…' as resolver,
  sum(filled_orders_volume_usd) as volume_usd
from dune.elsvv.omniston_daily_resolver
where resolver_id is not null and src_chain_id != dst_chain_id
group by 1, 2
order by 1
