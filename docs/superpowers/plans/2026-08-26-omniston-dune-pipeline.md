# Omniston → Dune Ingest Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily job that pulls the full Omniston order history and aggregate cubes from a public JSON-RPC API and republishes them as seven queryable tables in the operator's own Dune namespace.

**Architecture:** Two read modules (aggregate cubes, raw orders) feed a Dune upload client. Every run is a full refresh: fetch everything into memory first, then clear and re-insert each table. Fetching completes before any Dune mutation begins, so an API failure can never leave Dune empty.

**Tech Stack:** Python 3.11+, `requests`, `pytest`, `responses` (HTTP mocking), GitHub Actions.

This plan covers the pipeline only. The DuneSQL queries and the dashboard layout are a
separate plan, written after these tables exist and can be queried.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-08-26-omniston-dune-dashboard-design.md`.

- Omniston JSON-RPC endpoint: `https://omni-history.ston.fi/json-rpc`. No authentication.
- **An explicit `User-Agent` header is mandatory.** Python's default `urllib` UA gets a Cloudflare 403 (code 1010).
- **`time_range` windows must not exceed 31 days.** Use 30 to leave margin.
- **Never use the filters `resolver_id_in_list` or `integrator_address_in_list`.** They hang forever with no response and no error. The same fields work correctly as aggregate dimensions.
- **Every JSON-RPC response may omit `rows` / `orders` entirely.** An empty result is `{"jsonrpc":"2.0","id":N,"result":{}}`. Always use `.get(key, [])`.
- **Always request `finalized_orders_count` alongside any volume metric.** Rows whose requested metrics are all zero are dropped by the server.
- **Never hardcode the chain list.** `xlayer` appears in live data but not in the published protobuf.
- **Cross-chain means `src_chain_id != dst_chain_id`**, never "excluding TON to TON".
- Dune upload API base: `https://api.dune.com/api/v1`. Endpoints are `/uploads/*`; the legacy `/table/*` paths are removed.
- Dune column types available for uploads: `timestamp`, `double`, `string`, `uint256`. There is no `varchar`.
- Dune `/insert` is all-or-nothing: HTTP 200 means every row landed, any other status means none did.
- Raw token amounts are up to 256-bit and MUST be stored as `string` to preserve exact values. USD amounts and counts are `double`.
- Dune free tier: 2,500 credits/month, 100 MB storage, 15 requests/minute on write endpoints.

---

### Task 1: Project scaffolding and configuration

**Files:**
- Create: `pyproject.toml`
- Create: `src/omniston_dune/__init__.py`
- Create: `src/omniston_dune/config.py`
- Create: `.env.example`
- Create: `.gitignore`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.Settings` dataclass with fields `dune_api_key: str`, `dune_namespace: str`, `user_agent: str`, `history_start_ts: int`. Function `config.load_settings(env: Mapping[str, str] | None = None) -> Settings`. Raises `config.ConfigError` with a message naming the missing variable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest

from omniston_dune import config


def test_load_settings_reads_env():
    settings = config.load_settings(
        {"DUNE_API_KEY": "abc123", "DUNE_NAMESPACE": "my_user"}
    )
    assert settings.dune_api_key == "abc123"
    assert settings.dune_namespace == "my_user"
    # 2026-03-25 UTC: a deliberate week of margin before history begins.
    assert settings.history_start_ts == 1774396800
    assert "omniston-dune" in settings.user_agent


def test_load_settings_names_the_missing_variable():
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_settings({"DUNE_NAMESPACE": "my_user"})
    assert "DUNE_API_KEY" in str(excinfo.value)


def test_load_settings_allows_overriding_history_start():
    settings = config.load_settings(
        {
            "DUNE_API_KEY": "abc123",
            "DUNE_NAMESPACE": "my_user",
            "HISTORY_START_TS": "1780000000",
        }
    )
    assert settings.history_start_ts == 1780000000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'omniston_dune'`

- [ ] **Step 3: Write the scaffolding and implementation**

```toml
# pyproject.toml
[project]
name = "omniston-dune"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["requests>=2.31"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "responses>=0.25"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```python
# src/omniston_dune/__init__.py
"""Ingest Omniston swap history into Dune uploaded tables."""
```

```python
# src/omniston_dune/config.py
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# 2026-03-25T00:00:00Z, a week of margin before history begins in April 2026.
# Starting earlier costs one wasted request per cube and returns nothing.
DEFAULT_HISTORY_START_TS = 1774396800

USER_AGENT = "omniston-dune/0.1 (+https://docs.ston.fi/developer-section/omniston/history)"


class ConfigError(RuntimeError):
    """Raised when a required setting is absent."""


@dataclass(frozen=True)
class Settings:
    dune_api_key: str
    dune_namespace: str
    user_agent: str
    history_start_ts: int


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        dune_api_key=_require(env, "DUNE_API_KEY"),
        dune_namespace=_require(env, "DUNE_NAMESPACE"),
        user_agent=env.get("OMNISTON_USER_AGENT", USER_AGENT),
        history_start_ts=int(env.get("HISTORY_START_TS", DEFAULT_HISTORY_START_TS)),
    )
```

```
# .env.example
# Dune API key with Read/Write scope. Create at dune.com -> Settings -> API.
DUNE_API_KEY=
# Your Dune username, or team name. Tables land at dune.<namespace>.<table>.
DUNE_NAMESPACE=
```

```
# .gitignore
.env
__pycache__/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 4: Install and run the tests**

Run:
```bash
python -m pip install -e ".[dev]"
python -m pytest tests/test_config.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/omniston_dune/__init__.py src/omniston_dune/config.py tests/test_config.py
git commit -m "feat: project scaffolding and configuration"
```

---

### Task 2: Omniston JSON-RPC client

**Files:**
- Create: `src/omniston_dune/omniston.py`
- Test: `tests/test_omniston.py`

**Interfaces:**
- Consumes: `config.USER_AGENT`.
- Produces:
  - `omniston.JSONRPC_URL: str`
  - `omniston.OmnistonError(RuntimeError)`
  - `omniston.call(method: str, params: dict, *, session: requests.Session | None = None, user_agent: str = config.USER_AGENT, timeout: float = 60.0) -> dict` — returns the `result` object, which may be `{}`. Raises `OmnistonError` on a JSON-RPC `error` payload or a non-200 status.
  - `omniston.new_session() -> requests.Session`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_omniston.py
import pytest
import responses

from omniston_dune import omniston


@responses.activate
def test_call_returns_result_object():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "result": {"rows": [{"a": 1}]}},
    )
    result = omniston.call("Some.Method", {"x": 1})
    assert result == {"rows": [{"a": 1}]}


@responses.activate
def test_call_returns_empty_dict_when_result_has_no_rows_key():
    # The live API returns exactly this for a window with no data.
    responses.post(
        omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}}
    )
    result = omniston.call("Some.Method", {})
    assert result == {}
    assert result.get("rows", []) == []


@responses.activate
def test_call_raises_on_jsonrpc_error():
    responses.post(
        omniston.JSONRPC_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": 3, "message": "unknown dimension `nope`"},
        },
    )
    with pytest.raises(omniston.OmnistonError) as excinfo:
        omniston.call("Some.Method", {})
    assert "unknown dimension" in str(excinfo.value)


@responses.activate
def test_call_raises_on_http_error():
    responses.post(omniston.JSONRPC_URL, status=403, body="denied")
    with pytest.raises(omniston.OmnistonError) as excinfo:
        omniston.call("Some.Method", {})
    assert "403" in str(excinfo.value)


@responses.activate
def test_call_sends_explicit_user_agent():
    # A default urllib/requests UA gets a Cloudflare 403 in production.
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    omniston.call("Some.Method", {}, user_agent="test-agent/9")
    assert responses.calls[0].request.headers["User-Agent"] == "test-agent/9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_omniston.py -v`
Expected: FAIL — `ImportError: cannot import name 'omniston'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/omniston.py
from __future__ import annotations

import requests

from . import config

JSONRPC_URL = "https://omni-history.ston.fi/json-rpc"

LIST_METHOD = "stonfi.omni.history.v1.FinalizedOrdersRpc.List"
AGGREGATES_METHOD = "stonfi.omni.history.v1.AggregatesRpc.FinalizedOrderAggregates"


class OmnistonError(RuntimeError):
    """A JSON-RPC error payload, or a transport-level failure."""


def new_session() -> requests.Session:
    return requests.Session()


def call(
    method: str,
    params: dict,
    *,
    session: requests.Session | None = None,
    user_agent: str = config.USER_AGENT,
    timeout: float = 60.0,
) -> dict:
    """Invoke one JSON-RPC method and return its `result` object.

    The result may legitimately be an empty dict: the service omits `rows` and
    `orders` entirely when nothing matched. Callers must use `.get(key, [])`.

    A timeout is always set. Two filters (`resolver_id_in_list` and
    `integrator_address_in_list`) hang forever on the live service, and without
    a timeout an ingest run would stall indefinitely.
    """
    poster = session.post if session is not None else requests.post
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    headers = {"Content-Type": "application/json", "User-Agent": user_agent}

    try:
        response = poster(JSONRPC_URL, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise OmnistonError(f"{method} transport failure: {exc}") from exc

    if response.status_code != 200:
        raise OmnistonError(
            f"{method} returned HTTP {response.status_code}: {response.text[:200]}"
        )

    body = response.json()
    if "error" in body:
        raise OmnistonError(f"{method} failed: {body['error'].get('message', body['error'])}")

    return body.get("result", {})
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_omniston.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/omniston.py tests/test_omniston.py
git commit -m "feat: Omniston JSON-RPC client with explicit UA and empty-result handling"
```

---

### Task 3: Field flatteners

The API nests chain identity inside the value: an asset is `{"ton": {"jetton": "EQ..."}}`
and an address is `{"polygon": "0xabc..."}`. Every table needs these as flat columns.

**Files:**
- Create: `src/omniston_dune/flatten.py`
- Test: `tests/test_flatten.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `flatten.flatten_asset(asset: dict | None) -> tuple[str | None, str | None, str | None]` returning `(chain, kind, address)`.
  - `flatten.flatten_chain_address(value: dict | None) -> tuple[str | None, str | None]` returning `(chain, address)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flatten.py
from omniston_dune import flatten


def test_ton_native():
    assert flatten.flatten_asset({"ton": {"native": {}}}) == ("ton", "native", None)


def test_ton_jetton():
    asset = {"ton": {"jetton": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"}}
    assert flatten.flatten_asset(asset) == (
        "ton",
        "jetton",
        "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
    )


def test_evm_erc20():
    asset = {"bnb": {"erc20": "0x55d398326f99059fF775485246999027B3197955"}}
    assert flatten.flatten_asset(asset) == (
        "bnb",
        "erc20",
        "0x55d398326f99059fF775485246999027B3197955",
    )


def test_tron_trc20():
    asset = {"tron": {"trc20": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"}}
    assert flatten.flatten_asset(asset) == (
        "tron",
        "trc20",
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    )


def test_unknown_chain_passes_through():
    # `xlayer` is live but absent from the published protobuf. Nothing may
    # hardcode the chain list, or new chains vanish silently.
    asset = {"xlayer": {"erc20": "0xdeadbeef"}}
    assert flatten.flatten_asset(asset) == ("xlayer", "erc20", "0xdeadbeef")


def test_multi_token_standard_joins_contract_and_token_id():
    asset = {"ethereum": {"erc1155": {"contract_address": "0xabc", "token_id": "42"}}}
    assert flatten.flatten_asset(asset) == ("ethereum", "erc1155", "0xabc:42")


def test_missing_asset_is_all_none():
    assert flatten.flatten_asset(None) == (None, None, None)
    assert flatten.flatten_asset({}) == (None, None, None)


def test_chain_address():
    value = {"polygon": "0xcd93B163292D4848D35Df2bBC0e9c3ebe3991614"}
    assert flatten.flatten_chain_address(value) == (
        "polygon",
        "0xcd93B163292D4848D35Df2bBC0e9c3ebe3991614",
    )


def test_missing_chain_address_is_none():
    # integrator_address is absent on ~43% of orders.
    assert flatten.flatten_chain_address(None) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flatten.py -v`
Expected: FAIL — `ImportError: cannot import name 'flatten'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/flatten.py
from __future__ import annotations


def flatten_asset(asset: dict | None) -> tuple[str | None, str | None, str | None]:
    """Split `{"ton": {"jetton": "EQ..."}}` into ("ton", "jetton", "EQ...").

    The chain name is whatever key the service used; it is never validated
    against a fixed list, because live data contains chains absent from the
    published protobuf.
    """
    if not asset:
        return (None, None, None)

    chain = next(iter(asset))
    inner = asset.get(chain) or {}
    if not inner:
        return (chain, None, None)

    kind = next(iter(inner))
    value = inner[kind]

    if isinstance(value, dict):
        # `native` is an empty object; the 1155 standards carry two fields.
        contract = value.get("contract_address")
        if contract is None:
            return (chain, kind, None)
        return (chain, kind, f"{contract}:{value.get('token_id', '')}")

    return (chain, kind, value)


def flatten_chain_address(value: dict | None) -> tuple[str | None, str | None]:
    """Split `{"polygon": "0xabc"}` into ("polygon", "0xabc")."""
    if not value:
        return (None, None)
    chain = next(iter(value))
    return (chain, value[chain])
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_flatten.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/flatten.py tests/test_flatten.py
git commit -m "feat: flatten nested asset and chain-address shapes"
```

---

### Task 4: Aggregate cube fetching

**Files:**
- Create: `src/omniston_dune/cubes.py`
- Test: `tests/test_cubes.py`

**Interfaces:**
- Consumes: `omniston.call`, `omniston.AGGREGATES_METHOD`, `flatten.flatten_asset`, `flatten.flatten_chain_address`.
- Produces:
  - `cubes.iter_windows(start_ts: int, end_ts: int, window_days: int = 30) -> Iterator[tuple[int, int]]`
  - `cubes.ALL_METRICS: list[str]`
  - `cubes.fetch_rows(from_ts, to_ts, dimensions, metrics=None, *, session=None) -> list[dict]` — one API call, returns raw rows.
  - `cubes.fetch_cube(start_ts, end_ts, dimensions, metrics=None, *, session=None) -> list[dict]` — walks windows, concatenates raw rows.
  - `cubes.CUBE_SPECS: dict[str, list[str]]` mapping table name to its dimension list.
  - `cubes.normalise_row(row: dict) -> dict` — flattens nested dimensions and casts metric strings to floats.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cubes.py
import pytest
import responses

from omniston_dune import cubes, omniston


def test_iter_windows_never_exceeds_the_31_day_cap():
    start, end = 0, 100 * 86400
    windows = list(cubes.iter_windows(start, end))
    assert windows[0][0] == start
    assert windows[-1][1] == end
    for from_ts, to_ts in windows:
        assert to_ts - from_ts <= 31 * 86400
    # Windows must tile the range with no gaps.
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[1] == later[0]


def test_iter_windows_handles_a_range_shorter_than_one_window():
    assert list(cubes.iter_windows(1000, 2000)) == [(1000, 2000)]


def test_iter_windows_is_empty_when_start_is_not_before_end():
    assert list(cubes.iter_windows(5000, 5000)) == []


@responses.activate
def test_fetch_rows_always_requests_the_order_count():
    # Rows whose requested metrics are all zero are dropped by the server, so a
    # volume-only request silently loses chain pairs that had only failures.
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    cubes.fetch_rows(0, 100, ["src_chain_id"], metrics=["filled_orders_volume_usd"])
    body = responses.calls[0].request.body
    sent = body.decode() if isinstance(body, bytes) else body
    assert "finalized_orders_count" in sent


@responses.activate
def test_fetch_rows_survives_an_empty_result():
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    assert cubes.fetch_rows(0, 100, ["src_chain_id"]) == []


@responses.activate
def test_fetch_cube_concatenates_across_windows():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "result": {"rows": [{"src_chain_id": "ton"}]}},
    )
    rows = cubes.fetch_cube(0, 90 * 86400, ["src_chain_id"])
    assert len(responses.calls) == 3
    assert len(rows) == 3


def test_normalise_row_flattens_dimensions_and_casts_metrics():
    row = {
        "time_period": "2026-08-25T00:00:00Z",
        "src_chain_id": "bnb",
        "input_asset": {"bnb": {"erc20": "0x55d3"}},
        "integrator_address": {"ton": "EQAi"},
        "filled_orders_volume_usd": "110313.9388981324101512987747",
        "finalized_orders_count": "123",
    }
    out = cubes.normalise_row(row)
    assert out["day"] == "2026-08-25T00:00:00Z"
    assert out["src_chain_id"] == "bnb"
    assert out["input_asset_chain"] == "bnb"
    assert out["input_asset_kind"] == "erc20"
    assert out["input_asset_address"] == "0x55d3"
    assert out["integrator_chain"] == "ton"
    assert out["integrator_address"] == "EQAi"
    assert out["filled_orders_volume_usd"] == pytest.approx(110313.93889813241)
    assert out["finalized_orders_count"] == 123.0
    assert "time_period" not in out


def test_normalise_row_defaults_absent_metrics_to_zero():
    out = cubes.normalise_row({"src_chain_id": "ton", "finalized_orders_count": "4"})
    assert out["filled_orders_volume_usd"] == 0.0
    assert out["protocol_fees_usd"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cubes.py -v`
Expected: FAIL — `ImportError: cannot import name 'cubes'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/cubes.py
from __future__ import annotations

from collections.abc import Iterator, Sequence

import requests

from . import omniston
from .flatten import flatten_asset, flatten_chain_address

DAY = "TIME_GROUPING_DAY"

# The service caps a filtered range at 31 days. 30 leaves margin for the
# inclusive/exclusive boundary.
WINDOW_DAYS = 30

ALL_METRICS = [
    "finalized_orders_volume_usd",
    "filled_orders_volume_usd",
    "protocol_fees_usd",
    "integrator_fees_usd",
    "finalized_orders_count",
    "unique_trader_wallets_count",
]

# `unique_trader_wallets_count` is NOT additive across buckets. It is only
# meaningful in the cube whose grouping matches how it will be displayed, which
# is why `omniston_daily_total` exists as its own table.
CUBE_SPECS: dict[str, list[str]] = {
    "omniston_daily_total": [],
    "omniston_daily_chainpair": ["src_chain_id", "dst_chain_id", "status"],
    "omniston_daily_resolver": ["resolver_id", "status"],
    "omniston_daily_input_asset": ["input_asset"],
    "omniston_daily_output_asset": ["output_asset"],
    "omniston_daily_integrator": ["integrator_address"],
}

_NESTED_ASSET_DIMENSIONS = ("input_asset", "output_asset")
_NESTED_ADDRESS_DIMENSIONS = (
    "src_trader_address",
    "dst_trader_address",
    "src_resolver_address",
    "dst_resolver_address",
    "integrator_address",
)


def iter_windows(
    start_ts: int, end_ts: int, window_days: int = WINDOW_DAYS
) -> Iterator[tuple[int, int]]:
    """Tile [start_ts, end_ts) into contiguous windows within the 31-day cap."""
    step = window_days * 86400
    cursor = start_ts
    while cursor < end_ts:
        nxt = min(cursor + step, end_ts)
        yield (cursor, nxt)
        cursor = nxt


def fetch_rows(
    from_ts: int,
    to_ts: int,
    dimensions: Sequence[str],
    metrics: Sequence[str] | None = None,
    *,
    time_grouping: str | None = DAY,
    session: requests.Session | None = None,
) -> list[dict]:
    """One aggregate call. Returns raw rows, possibly empty."""
    requested = list(metrics) if metrics else list(ALL_METRICS)
    # A row is dropped when every requested metric is zero. Keeping the count in
    # the request guarantees a row exists whenever any order exists.
    if "finalized_orders_count" not in requested:
        requested.append("finalized_orders_count")

    params: dict = {
        "filters": [
            {"time_range": {"from_timestamp": str(from_ts), "to_timestamp": str(to_ts)}}
        ],
        "aggregates_list": {"values": requested},
    }
    if time_grouping:
        params["time_grouping"] = time_grouping
    if dimensions:
        params["dimensions"] = {"values": list(dimensions)}

    result = omniston.call(omniston.AGGREGATES_METHOD, params, session=session)
    return result.get("rows", [])


def fetch_cube(
    start_ts: int,
    end_ts: int,
    dimensions: Sequence[str],
    metrics: Sequence[str] | None = None,
    *,
    session: requests.Session | None = None,
) -> list[dict]:
    """Walk the whole range in legal windows and concatenate the raw rows."""
    rows: list[dict] = []
    for from_ts, to_ts in iter_windows(start_ts, end_ts):
        rows.extend(
            fetch_rows(from_ts, to_ts, dimensions, metrics, session=session)
        )
    return rows


def normalise_row(row: dict) -> dict:
    """Flatten nested dimensions and cast decimal-string metrics to floats."""
    out: dict = {"day": row.get("time_period")}

    for key in ("src_chain_id", "dst_chain_id", "resolver_id", "status"):
        out[key] = row.get(key)

    for key in _NESTED_ASSET_DIMENSIONS:
        chain, kind, address = flatten_asset(row.get(key))
        out[f"{key}_chain"] = chain
        out[f"{key}_kind"] = kind
        out[f"{key}_address"] = address

    for key in _NESTED_ADDRESS_DIMENSIONS:
        chain, address = flatten_chain_address(row.get(key))
        prefix = key.removesuffix("_address")
        out[f"{prefix}_chain"] = chain
        out[f"{prefix}_address"] = address

    for metric in ALL_METRICS:
        value = row.get(metric)
        out[metric] = float(value) if value not in (None, "") else 0.0

    return out
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_cubes.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/cubes.py tests/test_cubes.py
git commit -m "feat: aggregate cube fetching with 30-day windowing"
```

---

### Task 5: Raw order fetching

**Files:**
- Create: `src/omniston_dune/orders.py`
- Test: `tests/test_orders.py`

**Interfaces:**
- Consumes: `omniston.call`, `omniston.LIST_METHOD`, `flatten.*`.
- Produces:
  - `orders.lt_from_timestamp(ts: int) -> int` — `lt` is a nanosecond timestamp, so any date is directly seekable.
  - `orders.iter_orders(from_ts, to_ts, *, page_size=1000, session=None) -> Iterator[dict]`
  - `orders.flatten_order(order: dict) -> dict` — one flat row including `t_quote`, `t_decide`, `t_settle`, `t_total` in seconds.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orders.py
import responses

from omniston_dune import omniston, orders

SAMPLE = {
    "lt": "1787654164994885497",
    "status": "TRADE_STATUS_FULLY_FILLED",
    "quote_id": "3f65ab23",
    "input_asset": {"polygon": {"erc20": "0xC011"}},
    "output_asset": {"bnb": {"erc20": "0x55d3"}},
    "quote_input_units": "200000",
    "quote_output_units": "180576479361381644",
    "integrator_fee_pips": 0,
    "protocol_fee_pips": 300,
    "actual_input_units": "200000",
    "actual_output_units": "180522306417573230",
    "actual_protocol_fee_units": "54172943808414",
    "actual_integrator_fee_units": "0",
    "src_trader_address": {"polygon": "0xcd93"},
    "dst_trader_address": {"bnb": "0xcd93"},
    "src_resolver_address": {"polygon": "0x2b65"},
    "dst_resolver_address": {"bnb": "0x2b65"},
    "resolver_id": "EQDE_TwS",
    "quote_request_time": "1787653757",
    "quote_time": "1787654117",
    "order_create_time": "1787654124",
    "order_finalize_time": "1787654161",
}


def test_lt_from_timestamp_is_nanoseconds():
    assert orders.lt_from_timestamp(1787654164) == 1787654164_000_000_000


@responses.activate
def test_iter_orders_paginates_until_has_next_page_is_false():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="10")], "has_next_page": True}},
    )
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="20")], "has_next_page": False}},
    )
    result = list(orders.iter_orders(0, 100))
    assert [o["lt"] for o in result] == ["10", "20"]


@responses.activate
def test_iter_orders_omits_prev_lt_on_the_first_page_then_sends_it():
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"orders": [dict(SAMPLE, lt="10")], "has_next_page": True}},
    )
    responses.post(
        omniston.JSONRPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "result": {"orders": [], "has_next_page": False}},
    )
    list(orders.iter_orders(0, 100))
    assert "prev_lt" not in responses.calls[0].request.body.decode()
    assert '"prev_lt": "10"' in responses.calls[1].request.body.decode()


@responses.activate
def test_iter_orders_stops_on_an_empty_result_with_no_orders_key():
    responses.post(omniston.JSONRPC_URL, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    assert list(orders.iter_orders(0, 100)) == []


def test_flatten_order_computes_the_latency_funnel():
    row = orders.flatten_order(SAMPLE)
    assert row["t_quote"] == 360.0      # 1787654117 - 1787653757
    assert row["t_decide"] == 7.0       # 1787654124 - 1787654117
    assert row["t_settle"] == 37.0      # 1787654161 - 1787654124
    assert row["t_total"] == 404.0      # 1787654161 - 1787653757


def test_flatten_order_splits_assets_and_addresses():
    row = orders.flatten_order(SAMPLE)
    assert row["src_chain_id"] == "polygon"
    assert row["dst_chain_id"] == "bnb"
    assert row["input_asset_kind"] == "erc20"
    assert row["output_asset_address"] == "0x55d3"
    assert row["src_trader_address"] == "0xcd93"


def test_flatten_order_keeps_raw_units_as_strings():
    # These are up to 256-bit; float would silently lose precision.
    row = orders.flatten_order(SAMPLE)
    assert row["actual_output_units"] == "180522306417573230"
    assert isinstance(row["actual_output_units"], str)


def test_flatten_order_tolerates_missing_quote_output_units():
    # Absent on ~25% of orders, all from one resolver on TON->EVM routes.
    partial = dict(SAMPLE)
    del partial["quote_output_units"]
    row = orders.flatten_order(partial)
    assert row["quote_output_units"] is None


def test_flatten_order_tolerates_a_missing_integrator():
    # Absent on ~43% of orders; present exactly when the integrator fee is > 0.
    row = orders.flatten_order(SAMPLE)
    assert row["integrator_address"] is None
    assert row["integrator_chain"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_orders.py -v`
Expected: FAIL — `ImportError: cannot import name 'orders'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/orders.py
from __future__ import annotations

from collections.abc import Iterator

import requests

from . import omniston
from .flatten import flatten_asset, flatten_chain_address

PAGE_SIZE = 1000

# Raw token amounts, kept as strings because they are up to 256-bit.
_UNIT_FIELDS = (
    "quote_input_units",
    "quote_output_units",
    "actual_input_units",
    "actual_output_units",
    "actual_protocol_fee_units",
    "actual_integrator_fee_units",
)


def lt_from_timestamp(ts: int) -> int:
    """`lt` is a nanosecond timestamp anchored to finalization time."""
    return ts * 1_000_000_000


def iter_orders(
    from_ts: int,
    to_ts: int,
    *,
    page_size: int = PAGE_SIZE,
    session: requests.Session | None = None,
) -> Iterator[dict]:
    """Yield every finalized order created within [from_ts, to_ts).

    `time_range` filters on creation time while `lt` orders by finalization
    time. The first page omits `prev_lt` so scanning starts at the beginning of
    the filtered set, which is correct regardless of that skew.
    """
    prev_lt: str | None = None
    while True:
        params: dict = {
            "filters": [
                {"time_range": {"from_timestamp": str(from_ts), "to_timestamp": str(to_ts)}}
            ],
            "limit": page_size,
        }
        if prev_lt is not None:
            params["prev_lt"] = prev_lt

        result = omniston.call(omniston.LIST_METHOD, params, session=session)
        page = result.get("orders", [])
        if not page:
            return

        yield from page

        if not result.get("has_next_page"):
            return
        prev_lt = page[-1]["lt"]


def _seconds(order: dict, key: str) -> float | None:
    value = order.get(key)
    return float(value) if value not in (None, "") else None


def _gap(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return later - earlier


def flatten_order(order: dict) -> dict:
    """One flat row, with the four latency intervals precomputed in seconds."""
    requested = _seconds(order, "quote_request_time")
    quoted = _seconds(order, "quote_time")
    created = _seconds(order, "order_create_time")
    finalized = _seconds(order, "order_finalize_time")

    in_chain, in_kind, in_address = flatten_asset(order.get("input_asset"))
    out_chain, out_kind, out_address = flatten_asset(order.get("output_asset"))

    row: dict = {
        "lt": order["lt"],
        "status": order.get("status"),
        "quote_id": order.get("quote_id"),
        "src_chain_id": in_chain,
        "dst_chain_id": out_chain,
        "input_asset_chain": in_chain,
        "input_asset_kind": in_kind,
        "input_asset_address": in_address,
        "output_asset_chain": out_chain,
        "output_asset_kind": out_kind,
        "output_asset_address": out_address,
        "resolver_id": order.get("resolver_id"),
        "integrator_fee_pips": float(order.get("integrator_fee_pips") or 0),
        "protocol_fee_pips": float(order.get("protocol_fee_pips") or 0),
        "quote_request_time": requested,
        "quote_time": quoted,
        "order_create_time": created,
        "order_finalize_time": finalized,
        "t_quote": _gap(quoted, requested),
        "t_decide": _gap(created, quoted),
        "t_settle": _gap(finalized, created),
        "t_total": _gap(finalized, requested),
    }

    for field in _UNIT_FIELDS:
        value = order.get(field)
        row[field] = str(value) if value not in (None, "") else None

    for field in ("src_trader_address", "dst_trader_address",
                  "src_resolver_address", "dst_resolver_address",
                  "integrator_address"):
        chain, address = flatten_chain_address(order.get(field))
        prefix = field.removesuffix("_address")
        row[f"{prefix}_chain"] = chain
        row[f"{prefix}_address"] = address

    return row
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_orders.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/orders.py tests/test_orders.py
git commit -m "feat: paginated order fetching with precomputed latency funnel"
```

---

### Task 6: Dune uploads client

**Files:**
- Create: `src/omniston_dune/dune.py`
- Test: `tests/test_dune.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `dune.BASE_URL: str`
  - `dune.DuneError(RuntimeError)`
  - `dune.create_table(api_key, namespace, table_name, schema, *, description="", is_private=False) -> dict` — treats "already exists" as success.
  - `dune.clear_table(api_key, namespace, table_name) -> None`
  - `dune.insert_rows(api_key, namespace, table_name, rows, *, chunk_size=50_000) -> int` — returns the number of rows sent.
  - `dune.execute_query(api_key, query_id: int) -> str` — returns the execution id.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dune.py
import json

import pytest
import responses

from omniston_dune import dune


@responses.activate
def test_create_table_posts_the_schema():
    responses.post(f"{dune.BASE_URL}/uploads", json={"success": True})
    dune.create_table(
        "key", "me", "t", [{"name": "day", "type": "timestamp", "nullable": False}]
    )
    sent = json.loads(responses.calls[0].request.body)
    assert sent["namespace"] == "me"
    assert sent["table_name"] == "t"
    assert sent["schema"][0]["type"] == "timestamp"
    assert responses.calls[0].request.headers["X-DUNE-API-KEY"] == "key"


@responses.activate
def test_create_table_treats_an_existing_table_as_success():
    # Re-creating is expected on every run after the first.
    responses.post(
        f"{dune.BASE_URL}/uploads",
        status=409,
        json={"error": "table already exists"},
    )
    dune.create_table("key", "me", "t", [{"name": "day", "type": "timestamp"}])


@responses.activate
def test_create_table_raises_on_a_real_error():
    responses.post(f"{dune.BASE_URL}/uploads", status=400, json={"error": "bad schema"})
    with pytest.raises(dune.DuneError):
        dune.create_table("key", "me", "t", [{"name": "1bad", "type": "timestamp"}])


@responses.activate
def test_insert_rows_sends_ndjson():
    responses.post(f"{dune.BASE_URL}/uploads/me/t/insert", json={"rows_written": 2})
    sent = dune.insert_rows("key", "me", "t", [{"a": 1}, {"a": 2}])
    assert sent == 2
    request = responses.calls[0].request
    assert request.headers["Content-Type"] == "application/x-ndjson"
    lines = request.body.decode().strip().split("\n")
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"a": 2}]


@responses.activate
def test_insert_rows_chunks_large_payloads():
    responses.post(f"{dune.BASE_URL}/uploads/me/t/insert", json={})
    dune.insert_rows("key", "me", "t", [{"a": i} for i in range(5)], chunk_size=2)
    assert len(responses.calls) == 3


@responses.activate
def test_insert_rows_does_nothing_when_there_are_no_rows():
    assert dune.insert_rows("key", "me", "t", []) == 0
    assert len(responses.calls) == 0


@responses.activate
def test_clear_table_posts_to_the_clear_path():
    responses.post(f"{dune.BASE_URL}/uploads/me/t/clear", json={})
    dune.clear_table("key", "me", "t")
    assert responses.calls[0].request.url.endswith("/uploads/me/t/clear")


@responses.activate
def test_execute_query_returns_the_execution_id():
    responses.post(
        f"{dune.BASE_URL}/query/123/execute", json={"execution_id": "01ABC"}
    )
    assert dune.execute_query("key", 123) == "01ABC"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dune.py -v`
Expected: FAIL — `ImportError: cannot import name 'dune'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/dune.py
from __future__ import annotations

import json
from collections.abc import Sequence

import requests

BASE_URL = "https://api.dune.com/api/v1"

# /insert accepts up to 1.2GB per request. This project's largest table is a few
# megabytes, so chunking is a guard rail rather than a necessity.
CHUNK_SIZE = 50_000


class DuneError(RuntimeError):
    """A non-success response from the Dune API."""


def _headers(api_key: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"X-DUNE-API-KEY": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _fail(action: str, response: requests.Response) -> DuneError:
    return DuneError(f"{action} returned HTTP {response.status_code}: {response.text[:300]}")


def create_table(
    api_key: str,
    namespace: str,
    table_name: str,
    schema: Sequence[dict],
    *,
    description: str = "",
    is_private: bool = False,
) -> dict:
    """Create the table, treating an already-existing table as success.

    Creation costs credits, so this is called once per table per run and the
    conflict path is the normal path after the first run.
    """
    response = requests.post(
        f"{BASE_URL}/uploads",
        json={
            "namespace": namespace,
            "table_name": table_name,
            "description": description,
            "is_private": is_private,
            "schema": list(schema),
        },
        headers=_headers(api_key, "application/json"),
        timeout=60,
    )
    if response.status_code == 200:
        return response.json()
    if response.status_code == 409 or "already exists" in response.text.lower():
        return {"already_exists": True}
    raise _fail(f"create_table {namespace}.{table_name}", response)


def clear_table(api_key: str, namespace: str, table_name: str) -> None:
    """Empty a table. Dune cannot delete a date range, only the whole table."""
    response = requests.post(
        f"{BASE_URL}/uploads/{namespace}/{table_name}/clear",
        headers=_headers(api_key),
        timeout=120,
    )
    if response.status_code != 200:
        raise _fail(f"clear_table {namespace}.{table_name}", response)


def insert_rows(
    api_key: str,
    namespace: str,
    table_name: str,
    rows: Sequence[dict],
    *,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """Append rows as NDJSON.

    Each request is all-or-nothing: HTTP 200 means every row in that request
    landed, any other status means none of them did.
    """
    if not rows:
        return 0

    sent = 0
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        body = "\n".join(json.dumps(row, separators=(",", ":")) for row in chunk)
        response = requests.post(
            f"{BASE_URL}/uploads/{namespace}/{table_name}/insert",
            data=body.encode("utf-8"),
            headers=_headers(api_key, "application/x-ndjson"),
            timeout=300,
        )
        if response.status_code != 200:
            raise _fail(f"insert into {namespace}.{table_name}", response)
        sent += len(chunk)
    return sent


def execute_query(api_key: str, query_id: int) -> str:
    """Trigger a query run so the dashboard's cached result refreshes.

    Dashboard visitors see the last execution's result; uploading data without
    executing leaves the dashboard showing stale figures.
    """
    response = requests.post(
        f"{BASE_URL}/query/{query_id}/execute",
        headers=_headers(api_key, "application/json"),
        timeout=60,
    )
    if response.status_code != 200:
        raise _fail(f"execute_query {query_id}", response)
    return response.json().get("execution_id", "")
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_dune.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/dune.py tests/test_dune.py
git commit -m "feat: Dune uploads client with NDJSON insert and chunking"
```

---

### Task 7: Table schemas

**Files:**
- Create: `src/omniston_dune/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Consumes: `cubes.CUBE_SPECS`, `cubes.ALL_METRICS`.
- Produces:
  - `schemas.CUBE_COLUMNS: dict[str, list[dict]]` — table name to Dune schema.
  - `schemas.ORDERS_COLUMNS: list[dict]`
  - `schemas.TABLES: dict[str, list[dict]]` — every table name to its schema.
  - `schemas.project(row: dict, schema: Sequence[dict]) -> dict` — keeps exactly the schema's columns, filling absent ones with `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schemas.py
from omniston_dune import cubes, schemas


def test_every_cube_has_a_schema():
    assert set(schemas.CUBE_COLUMNS) == set(cubes.CUBE_SPECS)


def test_tables_includes_the_orders_table():
    assert "omniston_orders" in schemas.TABLES
    assert len(schemas.TABLES) == 7


def test_only_documented_dune_types_are_used():
    # Dune uploads accept timestamp, double, string, uint256. There is no varchar.
    allowed = {"timestamp", "double", "string", "uint256"}
    for table, columns in schemas.TABLES.items():
        for column in columns:
            assert column["type"] in allowed, f"{table}.{column['name']}"


def test_no_column_name_starts_with_a_digit_or_special_character():
    for table, columns in schemas.TABLES.items():
        for column in columns:
            assert column["name"][0].isalpha(), f"{table}.{column['name']}"


def test_raw_unit_columns_are_strings_to_preserve_256_bit_precision():
    by_name = {c["name"]: c for c in schemas.ORDERS_COLUMNS}
    assert by_name["actual_output_units"]["type"] == "string"
    assert by_name["quote_input_units"]["type"] == "string"


def test_usd_and_latency_columns_are_doubles():
    by_name = {c["name"]: c for c in schemas.ORDERS_COLUMNS}
    assert by_name["t_total"]["type"] == "double"
    chainpair = {c["name"]: c for c in schemas.CUBE_COLUMNS["omniston_daily_chainpair"]}
    assert chainpair["filled_orders_volume_usd"]["type"] == "double"


def test_output_asset_cube_carries_no_volume_column():
    # Both USD metrics are input-side; volume by bought asset does not exist.
    names = {c["name"] for c in schemas.CUBE_COLUMNS["omniston_daily_output_asset"]}
    assert "filled_orders_volume_usd" not in names
    assert "finalized_orders_count" in names


def test_project_keeps_schema_columns_and_fills_gaps():
    schema = [{"name": "a", "type": "string"}, {"name": "b", "type": "double"}]
    assert schemas.project({"a": "x", "z": 9}, schema) == {"a": "x", "b": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'schemas'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/schemas.py
from __future__ import annotations

from collections.abc import Sequence

from . import cubes


def _col(name: str, type_: str, nullable: bool = True) -> dict:
    return {"name": name, "type": type_, "nullable": nullable}


_DAY = [_col("day", "timestamp", nullable=False)]

_METRIC_COLUMNS = [_col(metric, "double") for metric in cubes.ALL_METRICS]

# `omniston_daily_output_asset` deliberately omits every USD metric: both volume
# figures are computed on the input side, so a volume attributed to the bought
# asset would be a different quantity wearing the same label.
_COUNT_ONLY = [_col("finalized_orders_count", "double")]

CUBE_COLUMNS: dict[str, list[dict]] = {
    "omniston_daily_total": _DAY + _METRIC_COLUMNS,
    "omniston_daily_chainpair": _DAY
    + [_col("src_chain_id", "string"), _col("dst_chain_id", "string"), _col("status", "string")]
    + _METRIC_COLUMNS,
    "omniston_daily_resolver": _DAY
    + [_col("resolver_id", "string"), _col("status", "string")]
    + _METRIC_COLUMNS,
    "omniston_daily_input_asset": _DAY
    + [
        _col("input_asset_chain", "string"),
        _col("input_asset_kind", "string"),
        _col("input_asset_address", "string"),
    ]
    + _METRIC_COLUMNS,
    "omniston_daily_output_asset": _DAY
    + [
        _col("output_asset_chain", "string"),
        _col("output_asset_kind", "string"),
        _col("output_asset_address", "string"),
    ]
    + _COUNT_ONLY,
    "omniston_daily_integrator": _DAY
    + [_col("integrator_chain", "string"), _col("integrator_address", "string")]
    + _METRIC_COLUMNS,
}

ORDERS_COLUMNS: list[dict] = [
    _col("lt", "string", nullable=False),
    _col("status", "string"),
    _col("quote_id", "string"),
    _col("src_chain_id", "string"),
    _col("dst_chain_id", "string"),
    _col("input_asset_chain", "string"),
    _col("input_asset_kind", "string"),
    _col("input_asset_address", "string"),
    _col("output_asset_chain", "string"),
    _col("output_asset_kind", "string"),
    _col("output_asset_address", "string"),
    _col("quote_input_units", "string"),
    _col("quote_output_units", "string"),
    _col("actual_input_units", "string"),
    _col("actual_output_units", "string"),
    _col("actual_protocol_fee_units", "string"),
    _col("actual_integrator_fee_units", "string"),
    _col("integrator_fee_pips", "double"),
    _col("protocol_fee_pips", "double"),
    _col("resolver_id", "string"),
    _col("src_trader_chain", "string"),
    _col("src_trader_address", "string"),
    _col("dst_trader_chain", "string"),
    _col("dst_trader_address", "string"),
    _col("src_resolver_chain", "string"),
    _col("src_resolver_address", "string"),
    _col("dst_resolver_chain", "string"),
    _col("dst_resolver_address", "string"),
    _col("integrator_chain", "string"),
    _col("integrator_address", "string"),
    _col("quote_request_time", "double"),
    _col("quote_time", "double"),
    _col("order_create_time", "double"),
    _col("order_finalize_time", "double"),
    _col("t_quote", "double"),
    _col("t_decide", "double"),
    _col("t_settle", "double"),
    _col("t_total", "double"),
]

TABLES: dict[str, list[dict]] = {**CUBE_COLUMNS, "omniston_orders": ORDERS_COLUMNS}


def project(row: dict, schema: Sequence[dict]) -> dict:
    """Keep exactly the schema's columns; absent keys become None.

    Dune rejects rows carrying keys the table does not have, and the cube
    normaliser deliberately emits a superset so one function serves every cube.
    """
    return {column["name"]: row.get(column["name"]) for column in schema}
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/schemas.py tests/test_schemas.py
git commit -m "feat: Dune table schemas for six cubes and the orders table"
```

---

### Task 8: Pipeline orchestration

The safety property this task exists to guarantee: **all fetching completes before any
Dune mutation begins.** Clearing a table and then failing to refill it would leave the
dashboard blank, and Dune offers no atomic swap.

**Files:**
- Create: `src/omniston_dune/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `config.Settings`, `cubes.*`, `orders.*`, `schemas.*`, `dune.*`.
- Produces:
  - `pipeline.PublishError(RuntimeError)`
  - `pipeline.build_datasets(settings, *, now_ts, session=None) -> dict[str, list[dict]]`
  - `pipeline.publish(settings, datasets, *, dune_module=dune) -> dict[str, int]`
  - `pipeline.run(settings, *, now_ts=None, query_ids=(), dune_module=dune) -> dict[str, int]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import pytest

from omniston_dune import config, pipeline, schemas

SETTINGS = config.Settings(
    dune_api_key="key", dune_namespace="me", user_agent="ua/1", history_start_ts=1000
)


class FakeDune:
    def __init__(self, fail_on_insert=False):
        self.calls = []
        self.fail_on_insert = fail_on_insert

    def create_table(self, api_key, namespace, table_name, schema, **kwargs):
        self.calls.append(("create", table_name))
        return {}

    def clear_table(self, api_key, namespace, table_name):
        self.calls.append(("clear", table_name))

    def insert_rows(self, api_key, namespace, table_name, rows, **kwargs):
        if self.fail_on_insert:
            raise RuntimeError("boom")
        self.calls.append(("insert", table_name))
        return len(rows)

    def execute_query(self, api_key, query_id):
        self.calls.append(("execute", query_id))
        return "exec"


def test_build_datasets_produces_one_entry_per_table(monkeypatch):
    monkeypatch.setattr(pipeline.cubes, "fetch_cube", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))
    datasets = pipeline.build_datasets(SETTINGS, now_ts=2000)
    assert set(datasets) == set(schemas.TABLES)


def test_build_datasets_projects_rows_onto_the_schema(monkeypatch):
    raw = {
        "time_period": "2026-08-25T00:00:00Z",
        "src_chain_id": "ton",
        "dst_chain_id": "bnb",
        "status": "TRADE_STATUS_FULLY_FILLED",
        "filled_orders_volume_usd": "12.5",
        "finalized_orders_count": "3",
    }
    monkeypatch.setattr(
        pipeline.cubes,
        "fetch_cube",
        lambda start, end, dims, **k: [raw] if dims == ["src_chain_id", "dst_chain_id", "status"] else [],
    )
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))

    datasets = pipeline.build_datasets(SETTINGS, now_ts=2000)
    row = datasets["omniston_daily_chainpair"][0]
    expected = {c["name"] for c in schemas.CUBE_COLUMNS["omniston_daily_chainpair"]}
    assert set(row) == expected
    assert row["filled_orders_volume_usd"] == 12.5


def test_run_fetches_everything_before_touching_dune(monkeypatch):
    # If a fetch raises, Dune must be untouched — no cleared, empty tables.
    monkeypatch.setattr(
        pipeline.cubes, "fetch_cube", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down"))
    )
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))
    fake = FakeDune()
    with pytest.raises(RuntimeError, match="api down"):
        pipeline.run(SETTINGS, now_ts=2000, dune_module=fake)
    assert fake.calls == []


def test_publish_creates_clears_then_inserts_in_that_order(monkeypatch):
    fake = FakeDune()
    pipeline.publish(SETTINGS, {"omniston_daily_total": [{"day": "x"}]}, dune_module=fake)
    assert fake.calls == [
        ("create", "omniston_daily_total"),
        ("clear", "omniston_daily_total"),
        ("insert", "omniston_daily_total"),
    ]


def test_publish_raises_when_fewer_rows_land_than_were_sent():
    # The spec requires verifying row counts after each run: clear-then-insert
    # has no rollback, so a short write leaves a truncated table.
    class ShortWriter(FakeDune):
        def insert_rows(self, api_key, namespace, table_name, rows, **kwargs):
            self.calls.append(("insert", table_name))
            return len(rows) - 1

    fake = ShortWriter()
    with pytest.raises(pipeline.PublishError, match="truncated"):
        pipeline.publish(
            SETTINGS,
            {"omniston_daily_total": [{"day": "x"}, {"day": "y"}]},
            dune_module=fake,
        )


def test_run_executes_the_dashboard_queries_last(monkeypatch):
    monkeypatch.setattr(pipeline.cubes, "fetch_cube", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.orders, "iter_orders", lambda *a, **k: iter([]))
    fake = FakeDune()
    pipeline.run(SETTINGS, now_ts=2000, query_ids=(11, 22), dune_module=fake)
    assert fake.calls[-2:] == [("execute", 11), ("execute", 22)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'pipeline'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/pipeline.py
from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence

import requests

from . import cubes, dune, orders, schemas
from .config import Settings

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    """A table was cleared but not fully refilled."""


def build_datasets(
    settings: Settings,
    *,
    now_ts: int | None = None,
    session: requests.Session | None = None,
) -> dict[str, list[dict]]:
    """Fetch every table's rows. Performs no writes.

    This is a full refresh of all history on every run. The dataset is small
    enough that this is cheap, and it makes retroactive corrections — orders
    that finalize days after they were created — self-healing.
    """
    now_ts = int(time.time()) if now_ts is None else now_ts
    start_ts = settings.history_start_ts

    datasets: dict[str, list[dict]] = {}

    for table_name, dimensions in cubes.CUBE_SPECS.items():
        schema = schemas.CUBE_COLUMNS[table_name]
        raw = cubes.fetch_cube(start_ts, now_ts, dimensions, session=session)
        datasets[table_name] = [
            schemas.project(cubes.normalise_row(row), schema) for row in raw
        ]
        log.info("fetched %s: %d rows", table_name, len(datasets[table_name]))

    datasets["omniston_orders"] = [
        schemas.project(orders.flatten_order(order), schemas.ORDERS_COLUMNS)
        for order in orders.iter_orders(start_ts, now_ts, session=session)
    ]
    log.info("fetched omniston_orders: %d rows", len(datasets["omniston_orders"]))

    return datasets


def publish(
    settings: Settings,
    datasets: dict[str, list[dict]],
    *,
    dune_module=dune,
) -> dict[str, int]:
    """Create, clear and refill each table. Only called once fetching succeeded."""
    written: dict[str, int] = {}
    for table_name, rows in datasets.items():
        schema = schemas.TABLES[table_name]
        dune_module.create_table(
            settings.dune_api_key,
            settings.dune_namespace,
            table_name,
            schema,
            description=f"Omniston history: {table_name}",
        )
        dune_module.clear_table(
            settings.dune_api_key, settings.dune_namespace, table_name
        )
        sent = dune_module.insert_rows(
            settings.dune_api_key, settings.dune_namespace, table_name, rows
        )
        if sent != len(rows):
            # The table was cleared immediately before this insert, so a short
            # write leaves it truncated -- neither empty nor intact. Say so
            # explicitly; a silent shortfall would understate the dashboard
            # until the next successful run.
            raise PublishError(
                f"{table_name}: cleared, then inserted {sent} of {len(rows)} rows. "
                f"The table is now truncated and must be refilled by a rerun."
            )
        written[table_name] = sent
        log.info("published %s: %d rows", table_name, written[table_name])
    return written


def run(
    settings: Settings,
    *,
    now_ts: int | None = None,
    query_ids: Sequence[int] | Iterable[int] = (),
    dune_module=dune,
) -> dict[str, int]:
    """Full refresh, then refresh the dashboard's cached query results."""
    session = requests.Session()
    try:
        datasets = build_datasets(settings, now_ts=now_ts, session=session)
    finally:
        session.close()

    written = publish(settings, datasets, dune_module=dune_module)

    for query_id in query_ids:
        dune_module.execute_query(settings.dune_api_key, query_id)
        log.info("triggered query %s", query_id)

    return written
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/omniston_dune/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline orchestration, fetching fully before any Dune write"
```

---

### Task 9: CLI and scheduled workflow

**Files:**
- Create: `src/omniston_dune/__main__.py`
- Create: `.github/workflows/daily.yml`
- Create: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `config.load_settings`, `pipeline.run`.
- Produces: `__main__.parse_args(argv: list[str]) -> argparse.Namespace` with attributes `query_ids: list[int]` and `dry_run: bool`; `__main__.main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from omniston_dune import __main__ as cli


def test_parse_args_defaults_to_no_query_ids():
    args = cli.parse_args([])
    assert args.query_ids == []
    assert args.dry_run is False


def test_parse_args_reads_comma_separated_query_ids():
    args = cli.parse_args(["--query-ids", "8309209,8309210"])
    assert args.query_ids == [8309209, 8309210]


def test_parse_args_supports_dry_run():
    assert cli.parse_args(["--dry-run"]).dry_run is True


def test_main_dry_run_fetches_without_publishing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli.config, "load_settings",
        lambda: cli.config.Settings("k", "ns", "ua", 1000),
    )
    monkeypatch.setattr(
        cli.pipeline, "build_datasets",
        lambda settings, **kwargs: calls.append("fetch") or {"t": [{"a": 1}]},
    )
    monkeypatch.setattr(
        cli.pipeline, "publish",
        lambda *a, **k: calls.append("publish") or {},
    )
    assert cli.main(["--dry-run"]) == 0
    assert calls == ["fetch"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `AttributeError: module 'omniston_dune.__main__' has no attribute 'parse_args'`

- [ ] **Step 3: Write the implementation**

```python
# src/omniston_dune/__main__.py
from __future__ import annotations

import argparse
import logging
import sys

from . import config, pipeline


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="omniston-dune",
        description="Refresh Omniston history tables in Dune.",
    )
    parser.add_argument(
        "--query-ids",
        default="",
        help="Comma-separated Dune query IDs to execute after uploading, so the "
             "dashboard's cached results refresh.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report row counts without writing anything to Dune.",
    )
    args = parser.parse_args(argv)
    args.query_ids = [int(part) for part in args.query_ids.split(",") if part.strip()]
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args(sys.argv[1:] if argv is None else argv)
    settings = config.load_settings()

    if args.dry_run:
        datasets = pipeline.build_datasets(settings)
        for table_name, rows in datasets.items():
            logging.info("%s: %d rows (dry run, nothing written)", table_name, len(rows))
        return 0

    written = pipeline.run(settings, query_ids=args.query_ids)
    for table_name, count in written.items():
        logging.info("%s: %d rows written", table_name, count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```yaml
# .github/workflows/daily.yml
name: Refresh Omniston tables in Dune

on:
  schedule:
    # 03:20 UTC daily. Off the hour, because scheduled runs cluster on the hour
    # and get delayed.
    - cron: "20 3 * * *"
  workflow_dispatch:

jobs:
  refresh:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install
        run: python -m pip install -e ".[dev]"

      - name: Test
        run: python -m pytest -q

      - name: Refresh
        env:
          DUNE_API_KEY: ${{ secrets.DUNE_API_KEY }}
          DUNE_NAMESPACE: ${{ secrets.DUNE_NAMESPACE }}
          DASHBOARD_QUERY_IDS: ${{ vars.DASHBOARD_QUERY_IDS }}
        run: python -m omniston_dune --query-ids "$DASHBOARD_QUERY_IDS"
```

```markdown
# omniston-dune

Publishes STON.fi Omniston swap history into Dune as seven queryable tables.

## Why a pipeline at all

Dune executes SQL only against data already inside Dune; it cannot call an
external API. Omniston history lives behind a public JSON-RPC service, so the
data has to be pushed in. Pressing "Run" on a Dune query re-reads the last
snapshot that was uploaded — it does not fetch anything new.

## Setup

    python -m pip install -e ".[dev]"
    cp .env.example .env    # fill in DUNE_API_KEY and DUNE_NAMESPACE

The API key needs `Read/Write` scope (dune.com → Settings → API).

## Running

    python -m omniston_dune --dry-run                    # fetch only, report counts
    python -m omniston_dune                              # full refresh
    python -m omniston_dune --query-ids 8309209,8309210  # and refresh dashboards

Every run is a full refresh of all history: each table is cleared and refilled.
That is deliberate. Some orders finalize days after they were created, so a
given day's success rate keeps changing for a while; recomputing everything
makes those corrections self-healing.

## Tables

| Table | Grain |
| --- | --- |
| `omniston_daily_total` | day |
| `omniston_daily_chainpair` | day × src chain × dst chain × status |
| `omniston_daily_resolver` | day × resolver × status |
| `omniston_daily_input_asset` | day × input asset |
| `omniston_daily_output_asset` | day × output asset (counts only) |
| `omniston_daily_integrator` | day × integrator |
| `omniston_orders` | one row per finalized order |

They land at `dune.<your-namespace>.<table>`.

Two things to know before querying them:

`unique_trader_wallets_count` is **not additive**. Summing it across chain pairs
counts one trader once per route. Use `omniston_daily_total` for headline user
counts, and add a cube if you need uniques at another grain.

Both USD metrics are **input-side**. `omniston_daily_output_asset` therefore
carries counts and no volume; volume attributed to a bought asset does not exist
in the source data.

## Cost

Roughly 14 write operations and a handful of query executions per day, against
2,500 free-tier credits per month. Check consumption at dune.com → Settings →
Billing during the first week rather than trusting this estimate.
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/omniston_dune/__main__.py .github/workflows/daily.yml README.md tests/test_cli.py
git commit -m "feat: CLI entry point, daily workflow, and README"
```

---

### Task 10: Live verification against the spec's acceptance figures

These are integration tests that hit the real API. They are the check that the
pipeline's definitions match the protocol's own published numbers. They are marked
so they can be excluded from the fast suite.

**Files:**
- Create: `tests/test_live_acceptance.py`
- Modify: `pyproject.toml` — register the `live` marker.

**Interfaces:**
- Consumes: `cubes.fetch_rows`, `orders.iter_orders`.
- Produces: nothing consumed by other tasks.

- [ ] **Step 1: Write the test**

```python
# tests/test_live_acceptance.py
"""Checks against the live Omniston API.

Run with:  python -m pytest -m live -v
These are excluded from the default suite because they need network access.
"""
import pytest

from omniston_dune import cubes, orders

pytestmark = pytest.mark.live

DAY = 86400
# 2026-08-24T00:00:00Z and 2026-08-25T00:00:00Z
AUG_24 = 1787529600
AUG_25 = 1787616000
AUG_26 = 1787702400


def _cross_chain_volume(from_ts: int, to_ts: int) -> float:
    rows = cubes.fetch_rows(
        from_ts,
        to_ts,
        ["src_chain_id", "dst_chain_id"],
        ["filled_orders_volume_usd"],
        time_grouping=None,
    )
    return sum(
        float(row.get("filled_orders_volume_usd", 0))
        for row in rows
        if row["src_chain_id"] != row["dst_chain_id"]
    )


@pytest.mark.parametrize(
    "from_ts,to_ts,expected",
    [(AUG_24, AUG_25, 126_583.0), (AUG_25, AUG_26, 149_371.0)],
)
def test_cross_chain_daily_volume_matches_the_published_figures(from_ts, to_ts, expected):
    """The team quoted these two days publicly. If we disagree, our
    cross-chain definition is wrong, not theirs."""
    actual = _cross_chain_volume(from_ts, to_ts)
    assert actual == pytest.approx(expected, rel=0.005)


def test_cross_chain_excludes_non_ton_intrachain():
    """Cross-chain is src != dst, never 'everything except TON to TON'.
    Non-TON intrachain swaps exist (base->base, bnb->bnb and others)."""
    rows = cubes.fetch_rows(
        AUG_24, AUG_26, ["src_chain_id", "dst_chain_id"], time_grouping=None
    )
    same_chain = [r for r in rows if r["src_chain_id"] == r["dst_chain_id"]]
    assert same_chain, "expected at least TON->TON to be present"


def test_only_two_statuses_occur():
    """No PARTIALLY_FILLED, CANCELLED or IN_PROGRESS has ever occurred.
    If this fails, the dashboard's success-rate logic needs revisiting."""
    rows = cubes.fetch_rows(AUG_24, AUG_26, ["status"], time_grouping=None)
    statuses = {row["status"] for row in rows}
    assert statuses <= {"TRADE_STATUS_FULLY_FILLED", "TRADE_STATUS_FAILED"}


def test_failed_orders_deliver_and_consume_nothing():
    """FAILED means the trader got nothing and paid nothing, so failed orders
    must never be counted in filled volume."""
    failed = [
        o
        for o in orders.iter_orders(AUG_25, AUG_26)
        if o["status"] == "TRADE_STATUS_FAILED"
    ]
    assert failed, "expected some failures in a full day"
    for order in failed:
        assert order.get("actual_input_units", "0") == "0"
        assert order.get("actual_output_units", "0") == "0"


def test_empty_window_returns_no_rows_without_raising():
    """A window before history began returns {"result": {}} with no rows key."""
    assert cubes.fetch_rows(1_500_000_000, 1_500_086_400, ["src_chain_id"]) == []


def test_chain_list_is_not_assumed():
    """Live data contains chains absent from the published protobuf. This test
    documents what is currently live rather than constraining it."""
    rows = cubes.fetch_rows(AUG_24, AUG_26, ["src_chain_id"], time_grouping=None)
    chains = {row["src_chain_id"] for row in rows}
    assert "ton" in chains
    assert len(chains) >= 2
```

- [ ] **Step 2: Register the marker**

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["live: hits the real Omniston API; needs network access"]
addopts = "-m 'not live'"
```

- [ ] **Step 3: Run the fast suite and confirm live tests are excluded**

Run: `python -m pytest -v`
Expected: all unit tests pass, live tests show as deselected

- [ ] **Step 4: Run the live suite**

Run: `python -m pytest -m live -v`
Expected: all pass. If the two volume figures disagree, stop and reconcile the
cross-chain definition before continuing — do not adjust the expected numbers.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_acceptance.py pyproject.toml
git commit -m "test: live acceptance checks against published Omniston figures"
```

---

### Task 11: First real run and cost measurement

No code. This task produces the operational facts the dashboard plan depends on.

- [ ] **Step 1: Create a Dune API key**

At dune.com → Settings → API, create a key with `Read/Write` scope.
Put it and your Dune username in `.env`.

- [ ] **Step 2: Dry run**

Run: `python -m omniston_dune --dry-run`
Expected: seven row counts logged. `omniston_orders` should be roughly 41,000 and
growing by about 250 a day. Nothing is written to Dune.

- [ ] **Step 3: Record the pre-run credit balance**

Note the credit balance at dune.com → Settings → Billing.

- [ ] **Step 4: Real run**

Run: `python -m omniston_dune`
Expected: seven tables created and filled. Verify in Dune's Data Explorer under
"My data" that each table exists with the expected row count.

- [ ] **Step 5: Record credits consumed and confirm storage**

Note the new balance. Record consumed credits in the README under Cost, replacing
the estimate with the measured figure. Confirm total storage is well under 100 MB.

- [ ] **Step 6: Sanity-check one table in SQL**

In the Dune query editor:

```sql
select
  sum(filled_orders_volume_usd) as cross_chain_volume_usd,
  sum(finalized_orders_count)   as orders
from dune.<your-namespace>.omniston_daily_chainpair
where src_chain_id != dst_chain_id
  and day >= timestamp '2026-08-24 00:00'
  and day <  timestamp '2026-08-25 00:00'
```

Expected: about 126,583 — the same figure the live acceptance test asserts.

- [ ] **Step 7: Commit the measured cost**

```bash
git add README.md
git commit -m "docs: record measured credit consumption from first run"
```

---

## Self-Review

**Spec coverage.** Section 2's verified facts appear as Global Constraints and as
tests: the hanging filters are absent from the codebase and named in the constraints;
the empty-`rows` shape is tested in Tasks 2, 4 and 10; the 31-day cap is enforced and
tested in Task 4; `finalized_orders_count` is force-included in Task 4; `xlayer` and
the no-hardcoded-chains rule are tested in Tasks 3 and 10; the two-status reality is
tested in Task 10; input-side USD semantics are encoded in Task 7's output-asset cube
and documented in the README; non-additive uniques drive the separate total cube in
Task 4 and are documented in Task 9. Section 3.1's components map to Tasks 1–9.
Section 3.2's seven tables are Task 7. Section 3.3's full refresh and fetch-before-write
ordering are Task 8, tested directly. Section 6's clear-then-fail risk is the subject of
`test_run_fetches_everything_before_touching_dune`. Section 7's acceptance figures are
Task 10.

Section 4 (dashboard sections and charts) and section 5's exclusions are deliberately
not covered here — they belong to the follow-up plan, which can only be written against
tables that exist.

**Placeholders.** None. Every code step carries complete code; every command has an
expected result. Task 11 is manual by nature and its steps are concrete actions with
observable outcomes.

**Type consistency.** `flatten_asset` returns a 3-tuple everywhere it is used;
`flatten_chain_address` returns a 2-tuple. `cubes.normalise_row` emits the superset of
column names that `schemas.project` then narrows, and the prefix convention
(`integrator_address` → `integrator_chain` + `integrator_address`) is identical in
`cubes.normalise_row` and `orders.flatten_order`. `dune` module function signatures used
by `pipeline.publish` match `FakeDune`'s in the tests and `dune.py`'s definitions.
`schemas.TABLES` keys match `cubes.CUBE_SPECS` keys plus `omniston_orders`, asserted in
Task 7.
