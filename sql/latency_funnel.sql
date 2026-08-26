-- Omniston · latency funnel
-- Four stages, from the moment a quote is requested to final settlement.
-- Percentiles, not averages: the tail is long and a mean would flatter it.
--
-- The stages do not add up to the total and are not meant to: the median of a
-- sum is not the sum of the medians, because no single swap sits at the median
-- of every stage. Read each row on its own.
with o as (
  select t_quote, t_decide, t_settle, t_total
  from dune.elsvv.omniston_orders
  where status = 'TRADE_STATUS_FULLY_FILLED' and src_chain_id != dst_chain_id
)
select 1 as ord, 'RFQ to quote'        as stage, approx_percentile(t_quote,0.5) as p50, approx_percentile(t_quote,0.9) as p90, approx_percentile(t_quote,0.99) as p99 from o
union all
select 2, 'quote to signature', approx_percentile(t_decide,0.5), approx_percentile(t_decide,0.9), approx_percentile(t_decide,0.99) from o
union all
select 3, 'settlement',         approx_percentile(t_settle,0.5), approx_percentile(t_settle,0.9), approx_percentile(t_settle,0.99) from o
union all
select 4, 'end to end',         approx_percentile(t_total,0.5),  approx_percentile(t_total,0.9),  approx_percentile(t_total,0.99)  from o
order by ord
