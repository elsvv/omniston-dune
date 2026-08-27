-- Omniston · when the data was last refreshed
--
-- Every table carries run_ts, stamped identically by the run that wrote it, so
-- a run that died partway leaves tables at different vintages. That is what
-- distinct_vintages counts: anything but 1 means a partial refresh.
--
-- The absolute timestamp is shown rather than an age in hours, and the reason
-- matters. A dashboard tile shows the last cached execution, and the pipeline
-- is what triggers executions -- so if the refresh stops happening, this query
-- stops re-running too. An age would freeze at whatever it last said and go on
-- claiming the data is fresh. A date keeps telling the truth without being
-- asked again.
select
  max(run_ts)            as refreshed_at,
  count(distinct run_ts) as distinct_vintages
from (
  select run_ts from dune.elsvv.omniston_daily_total
  union all select run_ts from dune.elsvv.omniston_daily_chainpair
  union all select run_ts from dune.elsvv.omniston_daily_resolver
  union all select run_ts from dune.elsvv.omniston_daily_input_asset
  union all select run_ts from dune.elsvv.omniston_daily_output_asset
  union all select run_ts from dune.elsvv.omniston_daily_integrator
  union all select run_ts from dune.elsvv.omniston_orders
)
