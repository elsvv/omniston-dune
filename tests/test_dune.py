import json

import pytest
import responses

from omniston_dune import dune


@pytest.fixture(autouse=True)
def no_write_throttle(monkeypatch):
    """Run the write throttle at zero delay.

    The real 4.5s spacing exists to stay under Dune's 15 writes/minute free
    tier; at that interval this file alone would take a minute. The interval
    is read from the module attribute on every call, so setting it to 0 here
    disables the sleep without touching the code path under test. The tests
    that exercise the throttle and the 429 retry set their own values.
    """
    monkeypatch.setattr(dune, "MIN_WRITE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(dune, "_last_write_monotonic", None)


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


@responses.activate
def test_writes_are_spaced_by_the_minimum_interval(monkeypatch):
    # One run issues 7 creates + 7 clears + 7 inserts plus an execute per
    # dashboard query -- over thirty writes against a 15/minute limit. The
    # throttle is what keeps that burst legal, so assert it actually sleeps
    # between consecutive writes rather than only on paper.
    slept = []
    monkeypatch.setattr(dune, "MIN_WRITE_INTERVAL_SECONDS", 4.5)
    monkeypatch.setattr(dune.time, "sleep", slept.append)
    responses.post(f"{dune.BASE_URL}/uploads/me/t/clear", json={})

    dune.clear_table("key", "me", "t")
    assert slept == []  # nothing to wait for on the first write of the process

    dune.clear_table("key", "me", "t")
    assert len(slept) == 1
    assert 4.0 < slept[0] <= 4.5


@responses.activate
def test_a_rate_limited_write_is_retried():
    # A 429 mid-publish would otherwise leave some tables refreshed and others
    # stale. Retry-After is honoured, so this retries immediately.
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/clear",
        status=429,
        headers={"Retry-After": "0"},
        json={"error": "rate limited"},
    )
    responses.post(f"{dune.BASE_URL}/uploads/me/t/clear", json={})

    dune.clear_table("key", "me", "t")
    assert len(responses.calls) == 2


@responses.activate
def test_persistent_rate_limiting_raises_after_the_retries_are_spent():
    for _ in range(3):
        responses.post(
            f"{dune.BASE_URL}/uploads/me/t/clear",
            status=429,
            headers={"Retry-After": "0"},
            json={"error": "rate limited"},
        )

    with pytest.raises(dune.DuneError, match="429"):
        dune.clear_table("key", "me", "t")
    # The original attempt plus MAX_RATE_LIMIT_RETRIES, and no more.
    assert len(responses.calls) == 3


@responses.activate
def test_retry_after_falls_back_to_a_full_minute_when_absent(monkeypatch):
    # The limit is per minute, so without a Retry-After header the only wait
    # guaranteed to clear the window is the whole window.
    slept = []
    monkeypatch.setattr(dune.time, "sleep", slept.append)
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/clear", status=429, json={"error": "slow down"}
    )
    responses.post(f"{dune.BASE_URL}/uploads/me/t/clear", json={})

    dune.clear_table("key", "me", "t")
    assert slept == [dune.DEFAULT_RETRY_AFTER_SECONDS]


@responses.activate
def test_insert_rows_reports_truncation_when_a_later_chunk_fails():
    # clear-then-insert has no rollback: publish clears the table before the
    # first chunk, so a chunk failing after an earlier one succeeded leaves the
    # table holding part of today's data. The error has to say that, or the
    # operator reads "insert failed" as "nothing was written".
    responses.post(f"{dune.BASE_URL}/uploads/me/t/insert", json={})
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/insert", status=500, json={"error": "boom"}
    )

    with pytest.raises(dune.DuneError, match="truncated") as excinfo:
        dune.insert_rows("key", "me", "t", [{"a": i} for i in range(4)], chunk_size=2)
    assert "2 of 4 rows" in str(excinfo.value)


@responses.activate
def test_insert_rows_first_chunk_failure_does_not_claim_truncation():
    # Nothing landed, so the table is simply empty -- today's message stands.
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/insert", status=500, json={"error": "boom"}
    )

    with pytest.raises(dune.DuneError) as excinfo:
        dune.insert_rows("key", "me", "t", [{"a": i} for i in range(4)], chunk_size=2)
    assert "truncated" not in str(excinfo.value)
