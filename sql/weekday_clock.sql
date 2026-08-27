-- Omniston · the shape of a week, UTC
--
-- The same question as the hour-of-day chart asked at the other resolution:
-- does cross-chain demand keep office hours, or does it run at the weekend
-- like the rest of crypto? Averaged over weeks for the same reason -- a raw
-- total per weekday grows with the age of the dashboard and says nothing.
--
-- Days with no swaps are zeros, not absences, so the sparse opening weeks
-- pull the averages down honestly instead of vanishing from them.
with jettons as (
  select address, symbol, decimals
  from (
    select address, symbol, decimals,
           row_number() over (partition by address order by _updated_at desc) as rn
    from ton.jetton_metadata
    where symbol is not null
  ) where rn = 1
),
priced as (
  select
    from_unixtime(o.order_create_time) as ts,
    o.src_trader_address as trader,
    case when coalesce(t.symbol, j.symbol)
              in ('USDT', 'USDC', 'USDG', 'pUSD', 'USD₮', 'USD₮0', 'USDe', 'DAI')
         then cast(o.actual_input_units as double)
              / power(10, coalesce(t.decimals, j.decimals)) end as usd
  from dune.elsvv.omniston_orders o
  left join tokens.erc20 t
    on t.blockchain = o.input_asset_chain
   and t.contract_address = try(from_hex(replace(o.input_asset_address, '0x', '')))
  left join jettons j
    -- Guarded on chain: the TON conversion is meaningless on an EVM address
    -- and from_base64 rejects one outright.
    on o.input_asset_chain = 'ton'
   and j.address = '0:' || upper(to_hex(substr(
         from_base64(replace(replace(o.input_asset_address, '-', '+'), '_', '/')), 3, 32)))
  where o.status = 'TRADE_STATUS_FULLY_FILLED'
    and o.src_chain_id != o.dst_chain_id
),
-- The last eight whole weeks. Averaging over all history would describe a
-- protocol that no longer exists: cross-chain barely traded before June, and
-- those dead weeks drag every hour toward zero, so the chart would answer
-- "what did an hour look like on average since launch" when the question is
-- what one looks like now. Eight weeks is long enough to give each weekday
-- eight samples and each hour fifty-six.
--
-- Whole days only. The first and the current day are both partial, and a
-- partial day dragged into an average of days makes every hour it does not
-- cover look quieter than it is.
bounds as (
  select greatest(date_add('day', 1, min(cast(ts as date))),
                  date_add('day', -56, current_date)) as d0,
         date_add('day', -1, current_date) as d1
  from priced
),
days as (
  select d from bounds cross join unnest(sequence(d0, d1, interval '1' day)) as t(d)
),
per_day as (
  select cast(ts as date) as day,
         count(*) as swaps,
         count(distinct trader) as traders,
         sum(usd) as volume_usd
  from priced
  group by 1
),
filled as (
  select days.d as day,
         coalesce(p.swaps, 0) as swaps,
         coalesce(p.traders, 0) as traders,
         coalesce(p.volume_usd, 0) as volume_usd
  from days
  left join per_day p on p.day = days.d
)
select
  format_datetime(cast(day as timestamp), 'EEEE') as weekday,
  avg(swaps) as avg_swaps,
  approx_percentile(swaps, 0.5) as median_swaps,
  avg(traders) as avg_traders,
  approx_percentile(traders, 0.5) as median_traders,
  avg(volume_usd) as avg_volume_usd,
  approx_percentile(volume_usd, 0.5) as median_volume_usd
from filled
group by 1, day_of_week(day)
order by day_of_week(day)
