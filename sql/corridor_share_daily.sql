-- Omniston · which routes carry the flow, day by day
--
-- Shares are computed here rather than left to the chart. Dune's stacked
-- charts stack but do not normalise -- neither `normalizeToPercentage` nor
-- `percentValues` has any effect on the rendered axis -- so a percentage that
-- is supposed to add to 100 has to arrive as a percentage.
--
-- Top eight corridors by lifetime volume keep their own band; the rest are
-- pooled, because a band thinner than a pixel is a colour, not information.
with legs as (
  select day, src_chain_id || ' → ' || dst_chain_id as corridor,
         sum(filled_orders_volume_usd) as usd
  from dune.elsvv.omniston_daily_chainpair
  where src_chain_id != dst_chain_id
  group by 1, 2
),
top as (
  select corridor from legs group by 1 order by sum(usd) desc limit 8
),
bucketed as (
  select day,
         case when corridor in (select corridor from top) then corridor else 'other' end as corridor,
         sum(usd) as usd
  from legs
  group by 1, 2
),
shares as (
  select day, corridor, usd, sum(usd) over (partition by day) as day_total
  from bucketed
  where usd > 0
)
-- Days under $1,000 are dropped. In the first weeks a single trade made one
-- corridor 100% of the day, which is true and tells you nothing.
-- A fraction, not a percentage: Dune's percent tick format multiplies by a
-- hundred itself, so handing it percentages labels the axis 10000%.
select day, corridor, usd / day_total as share
from shares
where day_total >= 1000
order by day
