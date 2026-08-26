-- Omniston · what traders sell, as a share of volume
--
-- Volume is measured on the input side of a trade, which is the only side the
-- protocol prices. Symbols come from Dune's own token metadata rather than a
-- hand-kept list.
--
-- TON addresses need converting first. Omniston reports jettons in the
-- user-friendly base64 form (EQCxE6mU…) while ton.jetton_metadata stores the
-- raw form (0:B113A9…). The friendly form is 36 bytes of base64url — one flag
-- byte, one workchain byte, the 32-byte hash, then a checksum — so the hash is
-- bytes 3 through 34. jetton_metadata also carries several rows per address as
-- metadata is revised, hence the dedupe.
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
    input_asset_chain   as chain,
    input_asset_kind    as kind,
    input_asset_address as address,
    case when input_asset_chain = 'ton' and input_asset_address is not null
         then '0:' || upper(to_hex(substr(
                from_base64(replace(replace(input_asset_address, '-', '+'), '_', '/')), 3, 32)))
    end as ton_raw,
    sum(filled_orders_volume_usd) as volume_usd
  from dune.elsvv.omniston_daily_input_asset
  where src_chain_id != dst_chain_id
  group by 1, 2, 3, 4
),
named as (
  select
    coalesce(case when a.kind = 'native' then upper(a.chain) end,
             t.symbol, j.symbol, substr(a.address, 1, 6) || '…')
      || ' · ' || a.chain as asset,
    sum(a.volume_usd) as volume_usd
  from assets a
  left join tokens.erc20 t
    on t.blockchain = a.chain
   and t.contract_address = try(from_hex(replace(a.address, '0x', '')))
  left join jettons j on j.address = a.ton_raw
  group by 1
  having sum(a.volume_usd) > 0
),
ranked as (
  select asset, volume_usd, row_number() over (order by volume_usd desc) as rn from named
)
select
  case when rn <= 8 then asset else 'Everything else' end as asset,
  sum(volume_usd) as volume_usd
from ranked
group by 1
order by volume_usd desc
