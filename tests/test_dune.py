import json

import pytest
import requests
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
def test_create_table_on_a_201_returns_the_body_and_does_not_raise():
    # Verified live: creating a brand-new table returns 201, not 200. This is
    # the exact status that broke the first real run.
    body = {
        "namespace": "me",
        "table_name": "t",
        "full_name": "dune.me.t",
        "example_query": "select * from dune.me.t",
        "message": "Table created successfully",
    }
    responses.post(f"{dune.BASE_URL}/uploads", status=201, json=body)
    result = dune.create_table("key", "me", "t", [{"name": "day", "type": "timestamp"}])
    assert result == body


@responses.activate
def test_create_table_treats_an_existing_table_as_success():
    # Re-creating is expected on every run after the first. Verified live:
    # this comes back as 200 with `already_existed: true`, not a 4xx.
    responses.post(
        f"{dune.BASE_URL}/uploads",
        status=200,
        json={
            "namespace": "me",
            "table_name": "t",
            "full_name": "dune.me.t",
            "already_existed": True,
            "message": "Table already existed and matched the request",
        },
    )
    result = dune.create_table("key", "me", "t", [{"name": "day", "type": "timestamp"}])
    assert result["already_existed"] is True


@responses.activate
def test_create_table_raises_on_a_500():
    responses.post(
        f"{dune.BASE_URL}/uploads",
        status=500,
        json={"error": "internal error"},
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
def test_insert_rows_sums_rows_written_across_chunks():
    # The count returned has to be real: publish compares it against the
    # number of rows it tried to send, and a Dune-side shortfall on one
    # chunk must show up in the total even though the other chunk was fine.
    responses.post(f"{dune.BASE_URL}/uploads/me/t/insert", json={"rows_written": 2})
    responses.post(f"{dune.BASE_URL}/uploads/me/t/insert", json={"rows_written": 1})
    written = dune.insert_rows(
        "key", "me", "t", [{"a": i} for i in range(4)], chunk_size=2
    )
    assert written == 3


@responses.activate
def test_insert_rows_falls_back_to_chunk_length_when_rows_written_is_absent():
    # A response missing `rows_written` must not be read as zero -- that would
    # turn a healthy insert into a spurious PublishError in the caller.
    responses.post(f"{dune.BASE_URL}/uploads/me/t/insert", json={})
    written = dune.insert_rows("key", "me", "t", [{"a": 1}, {"a": 2}])
    assert written == 2


@responses.activate
def test_insert_rows_treats_a_201_as_success():
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/insert", status=201, json={"rows_written": 1}
    )
    written = dune.insert_rows("key", "me", "t", [{"a": 1}])
    assert written == 1


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
def test_clear_table_accepts_any_2xx_status():
    # Widened alongside create_table's 201: this API's status codes are not
    # fully documented, and rejecting an unexpected-but-successful clear is
    # the dangerous direction, not accepting one.
    responses.post(f"{dune.BASE_URL}/uploads/me/t/clear", status=202, json={})
    dune.clear_table("key", "me", "t")


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


def test_retry_after_is_clamped_to_the_cap():
    # An uncapped wait here would hang an unattended nightly run until an
    # external timeout killed it mid-publish, instead of failing fast with a
    # clean DuneError. Asserting on the returned value (not a real sleep)
    # keeps this instant regardless of the cap's size.
    response = requests.Response()
    response.headers["Retry-After"] = "999999999"
    assert dune._retry_after_seconds(response) == dune.MAX_RETRY_AFTER_SECONDS


def test_retry_after_under_the_cap_is_returned_unchanged():
    # Proves the clamp only bites absurd values and leaves ordinary,
    # well-behaved Retry-After headers alone.
    response = requests.Response()
    response.headers["Retry-After"] = "3"
    assert dune._retry_after_seconds(response) == 3.0


@responses.activate
def test_a_transport_failure_surfaces_as_a_dune_error(monkeypatch):
    # A connection reset used to escape as a raw requests.ConnectionError.
    # __main__ catches this project's own error types and nothing else, so the
    # operator got a bare traceback instead of the message saying the table had
    # already been cleared and is now empty.
    slept = []
    monkeypatch.setattr(dune.time, "sleep", slept.append)
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/clear",
        body=requests.ConnectionError("connection reset by peer"),
    )

    with pytest.raises(dune.DuneError, match="transport failure") as excinfo:
        dune.clear_table("key", "me", "t")
    assert "connection reset by peer" in str(excinfo.value)
    # Retried with exponential backoff, then bounded.
    assert len(responses.calls) == dune.MAX_TRANSIENT_RETRIES + 1
    assert slept == [2.0, 4.0, 8.0]


@responses.activate
def test_a_transient_gateway_error_is_retried(monkeypatch):
    # One 502 from either service used to abort the whole run. Across a hundred
    # nightly runs that is a near-certainty, and mid-publish it leaves some
    # tables refreshed and others stale.
    slept = []
    monkeypatch.setattr(dune.time, "sleep", slept.append)
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/clear", status=502, json={"error": "bad gateway"}
    )
    responses.post(f"{dune.BASE_URL}/uploads/me/t/clear", json={})

    dune.clear_table("key", "me", "t")
    assert len(responses.calls) == 2
    # Exponential backoff, not the 429 path's Retry-After: gateways do not
    # send that header.
    assert slept == [dune.TRANSIENT_BACKOFF_SECONDS]


@responses.activate
def test_persistent_gateway_errors_raise_after_the_retries_are_spent(monkeypatch):
    slept = []
    monkeypatch.setattr(dune.time, "sleep", slept.append)
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/clear", status=503, json={"error": "unavailable"}
    )

    with pytest.raises(dune.DuneError, match="503"):
        dune.clear_table("key", "me", "t")
    # The original attempt plus MAX_TRANSIENT_RETRIES, and no more.
    assert len(responses.calls) == dune.MAX_TRANSIENT_RETRIES + 1
    assert slept == [2.0, 4.0, 8.0]


@responses.activate
def test_a_client_error_is_never_retried(monkeypatch):
    # Guard on the boundary: a 500 or a 400 will fail identically forever, so
    # retrying only delays the failure. Only the gateway statuses are transient.
    monkeypatch.setattr(dune.time, "sleep", lambda seconds: None)
    responses.post(
        f"{dune.BASE_URL}/uploads/me/t/clear", status=500, json={"error": "boom"}
    )

    with pytest.raises(dune.DuneError, match="500"):
        dune.clear_table("key", "me", "t")
    assert len(responses.calls) == 1
