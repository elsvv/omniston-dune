-- Omniston · what the money did after it arrived
--
-- The flow figure understates the story by a wide margin, because a trader who
-- recycles a deposit through many positions shows up once as a deposit and many
-- times as wagered volume. This query measures the second number.
--
-- Identification is strict rather than heuristic. Polymarket issues every user
-- their own Deposit Wallet from a documented CREATE2 factory
-- (0x00000000000Fb5C9ADea0298D729A0CB3823Cc07, "Polymarket: Deposit Wallet
-- Factory Proxy"), so a destination address either was deployed by that factory
-- or it was not. A control check on the same TON-to-Polygon route carrying plain
-- USDC instead of pUSD matched zero deposit wallets, which is what rules out the
-- route itself explaining the signal.
--
-- Caveat carried onto the dashboard: wagered volume is gross. Someone cycling
-- $100 through forty positions contributes $4,000 to it. It is not profit and
-- not net deposits.
with deposit_wallets as (
  select distinct address as addr
  from polygon.creation_traces
  where "from" = 0x00000000000fb5c9adea0298d729a0cb3823cc07
),
funded as (
  select
    from_hex(o.dst_trader_address) as addr,
    min(from_unixtime(o.order_finalize_time)) as first_deposit,
    sum(cast(o.actual_output_units as double) / 1e6) as deposited_usd
  from dune.elsvv.omniston_orders o
  join deposit_wallets w on w.addr = from_hex(o.dst_trader_address)
  where o.dst_chain_id = 'polygon' and o.src_chain_id != o.dst_chain_id
    and o.status = 'TRADE_STATUS_FULLY_FILLED'
    and o.output_asset_address = '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'
  group by 1
),
-- A wallet can sit on either side of a trade, so both sides are unioned. The
-- block_month bound is required: without it the scan is slow enough to time
-- out. April is comfortably before the first cross-chain swap of any kind
-- (June), so nothing is lost -- but it is a floor on Polymarket trades, not on
-- deposits, and would have to move if history ever started earlier.
traded as (
  select addr, count(*) as trades, sum(amount) as wagered_usd, min(block_time) as first_trade
  from (
    select maker as addr, amount, block_time from polymarket_polygon.market_trades
      where block_month >= date '2026-04-01'
    union all
    select taker, amount, block_time from polymarket_polygon.market_trades
      where block_month >= date '2026-04-01'
  ) both_sides
  group by 1
)
select
  count(*)                                                as wallets_funded,
  count_if(t.addr is not null)                            as wallets_that_traded,
  count_if(t.first_trade > f.first_deposit)               as new_traders,
  round(sum(f.deposited_usd))                             as deposited_usd,
  round(sum(t.wagered_usd))                               as wagered_usd,
  round(sum(t.wagered_usd) / nullif(sum(f.deposited_usd), 0), 1) as multiple,
  -- Seconds, not minutes. date_diff truncates before the percentile is taken,
  -- so measuring in minutes scored a 47-second wallet as zero and the median
  -- came out as the bare "1 min" that hid the real figure.
  --
  -- Both lag figures count only wallets whose first bet came after their first
  -- deposit -- the same 2,440 wallets as new_traders. A wallet that already
  -- traded before it was funded has a negative lag, which is a true fact about
  -- a different question, and one that never traded has no lag at all.
  --
  -- The clock starts at order_finalize_time, when the money reached the wallet.
  -- These figures therefore measure what someone does once funded, not how long
  -- Omniston took to fund them: the swap is already over when the clock starts.
  round(approx_percentile(
    case when t.first_trade > f.first_deposit
         then date_diff('second', f.first_deposit, t.first_trade) end, 0.5)) as median_lag_seconds,
  100.0 * count_if(t.first_trade > f.first_deposit
                   and date_diff('second', f.first_deposit, t.first_trade) <= 60)
        / nullif(count_if(t.first_trade > f.first_deposit), 0) as within_a_minute_pct
from funded f
left join traded t on t.addr = f.addr
