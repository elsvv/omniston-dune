-- Omniston · share of integrator fees, by integrator
-- Everything past the top six is a rounding error, so it is pooled rather than
-- drawn as slices too thin to read.
select
  case when rn <= 6 then integrator else 'Everything else' end as integrator,
  sum(fees_usd) as fees_usd
from (
  select
    coalesce(substr(integrator_address, 1, 4) || '…' || substr(integrator_address, -4),
             'unattributed') as integrator,
    sum(integrator_fees_usd) as fees_usd,
    row_number() over (order by sum(integrator_fees_usd) desc) as rn
  from dune.elsvv.omniston_daily_integrator
  where src_chain_id != dst_chain_id
  group by 1
  having sum(integrator_fees_usd) > 0
)
group by 1
order by fees_usd desc
