-- Omniston · what traders buy, as a share of swaps
--
-- Counted in swaps rather than dollars on purpose: volume is priced on the
-- side sold, so a dollar figure attached to the asset bought would be a
-- different quantity wearing the same label. Symbol resolution and the TON
-- address conversion work exactly as in assets_sold.
with jettons as (
  select address, symbol
  from (
    select address, symbol,
           row_number() over (partition by address order by _updated_at desc) as rn
    from ton.jetton_metadata
    where symbol is not null
  ) where rn = 1
),
assets as (
  select
    output_asset_chain   as chain,
    output_asset_kind    as kind,
    output_asset_address as address,
    case when output_asset_chain = 'ton' and output_asset_address is not null
         then '0:' || upper(to_hex(substr(
                from_base64(replace(replace(output_asset_address, '-', '+'), '_', '/')), 3, 32)))
    end as ton_raw,
    sum(finalized_orders_count) as swaps
  from dune.elsvv.omniston_daily_output_asset
  where src_chain_id != dst_chain_id
  group by 1, 2, 3, 4
),
named as (
  select
    coalesce(case when a.kind = 'native' then upper(a.chain) end,
             t.symbol, j.symbol, substr(a.address, 1, 6) || '…') as asset,
    sum(a.swaps) as swaps
  from assets a
  left join tokens.erc20 t
    on t.blockchain = a.chain
   and t.contract_address = try(from_hex(replace(a.address, '0x', '')))
  left join jettons j on j.address = a.ton_raw
  group by 1
  having sum(a.swaps) > 0
),
ranked as (
  select asset, swaps, row_number() over (order by swaps desc) as rn from named
)
select
  case when rn <= 8 then asset else 'Everything else' end as asset,
  sum(swaps) as swaps
from ranked
group by 1
order by swaps desc
