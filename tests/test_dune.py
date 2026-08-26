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
def test_create_table_treats_a_400_already_exists_as_success():
    # Dune's docs only promise creating a duplicate table "will fail", without
    # committing to a status code, so 400 is accepted alongside 409.
    responses.post(
        f"{dune.BASE_URL}/uploads",
        status=400,
        json={"error": "table already exists"},
    )
    dune.create_table("key", "me", "t", [{"name": "day", "type": "timestamp"}])


@responses.activate
def test_create_table_raises_on_a_server_error_even_if_it_mentions_already_exists():
    # Regression guard: a 5xx whose body happens to contain "already exists"
    # (e.g. a leaked database error) must never be mistaken for a
    # pre-existing table, since that would skip creation and let the run
    # proceed to clear/insert against a table that was never created.
    responses.post(
        f"{dune.BASE_URL}/uploads",
        status=500,
        json={"error": "internal error: relation already exists"},
    )
    with pytest.raises(dune.DuneError):
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
