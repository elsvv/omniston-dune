-- Omniston · what a typical trade is worth
-- Bucketed rather than averaged: the distribution is heavily skewed, and a
-- mean would describe a trade almost nobody makes.
with sized as (
  select
    case
      when filled_orders_volume_usd / nullif(finalized_orders_count, 0) <     1 then '1  under $1'
      when filled_orders_volume_usd / nullif(finalized_orders_count, 0) <    10 then '2  $1 to $10'
      when filled_orders_volume_usd / nullif(finalized_orders_count, 0) <   100 then '3  $10 to $100'
      when filled_orders_volume_usd / nullif(finalized_orders_count, 0) <  1000 then '4  $100 to $1k'
      when filled_orders_volume_usd / nullif(finalized_orders_count, 0) < 10000 then '5  $1k to $10k'
      else '6  over $10k'
    end as bucket,
    finalized_orders_count as orders,
    filled_orders_volume_usd as usd
  from dune.elsvv.omniston_daily_chainpair
  where finalized_orders_count > 0 and filled_orders_volume_usd > 0
    and src_chain_id != dst_chain_id
)
select substr(bucket, 4) as trade_size, sum(orders) as orders, sum(usd) as volume_usd
from sized group by bucket order by bucket
