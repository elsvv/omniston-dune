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
