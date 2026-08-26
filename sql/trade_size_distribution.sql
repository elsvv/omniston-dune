-- Omniston · what a cross-chain trade is actually worth
--
-- Measured per trade. The earlier version bucketed the average trade size of
-- each (day, route, status) group taken from the daily cube, which is the
-- distribution of route-day averages wearing this one's name: a day where one
-- $10,000 trade sat beside a hundred $8 trades landed the whole group in a
-- single middling bucket.
--
-- Trade values come from the raw input units. Not one cross-chain input is a
-- native coin -- every single one is a token -- and they are dollar
-- stablecoins, so units scaled by the token's decimals is the dollar value,
-- with no price feed involved. Anything whose input token has no decimals in
-- Dune's metadata, or is not a dollar stablecoin, is dropped rather than
-- guessed at.
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
    coalesce(t.symbol, j.symbol) as symbol,
    cast(o.actual_input_units as double)
      / power(10, coalesce(t.decimals, j.decimals)) as usd
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
    and o.actual_input_units is not null
),
sized as (
  select
    case
      when usd <     1 then '1  under $1'
      when usd <    10 then '2  $1 to $10'
      when usd <   100 then '3  $10 to $100'
      when usd <= 1000 then '4  $100 to $1,000'
      else                  '5  over $1,000'
    end as bucket,
    usd
  from priced
  where usd is not null
    and symbol in ('USDT', 'USDC', 'USDG', 'pUSD', 'USD₮', 'USD₮0', 'USDe', 'DAI')
)
select substr(bucket, 4) as trade_size, count(*) as orders, sum(usd) as volume_usd
from sized
group by bucket
order by bucket
