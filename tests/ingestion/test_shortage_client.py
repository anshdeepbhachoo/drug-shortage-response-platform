"""Mocked tests for the shortage API client and record validation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from ingestion.shortage_client import ShortageAPIError, ShortageClient, validate_search_payload
from ingestion.shortage_ingest import (
    _normalize_din,
    is_newer_or_equal,
    run_ingest,
    validate_record,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "shortage_search_page.json"


def _fixture_page() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_login_reads_auth_token_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/login")
        return httpx.Response(200, headers={"auth-token": "tok-123"}, json={"user": {}})

    client = ShortageClient(email="a@b.com", password="secret", base_url="https://example.test/api/v1")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert client.login() == "tok-123"
    client.close()


def test_login_missing_credentials() -> None:
    client = ShortageClient(email="", password="", base_url="https://example.test/api/v1")
    with pytest.raises(ShortageAPIError, match="must be set"):
        client.login()
    client.close()


def test_search_page_uses_token_and_returns_list() -> None:
    page = _fixture_page()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, headers={"auth-token": "tok-123"}, json={"user": {}})
        assert request.headers.get("auth-token") == "tok-123"
        assert request.url.path.endswith("/search")
        return httpx.Response(200, json=page)

    with ShortageClient(
        email="a@b.com", password="secret", base_url="https://example.test/api/v1"
    ) as client:
        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        result = client.search_page(limit=100, type="shortage")
        assert result["total"] == 2
        assert len(result["data"]) == 2


def test_validate_record_reads_nested_api_fields() -> None:
    page = _fixture_page()
    first, err, _ = validate_record(page["data"][0])
    assert err is None
    assert first is not None
    assert first["report_id"] == 82552
    assert first["din"] == "02371234"
    assert first["brand_name"] == "EXAMPLE-DRUG 10MG"
    assert first["report_type"] == "shortage"
    assert first["ingredient"] == "DILTIAZEM"
    assert first["shortage_reason"] == "Disruption of the manufacture of the drug."
    assert first["drug_code"] == 66031
    assert first["tier3"] is False

    second, err, _ = validate_record(page["data"][1])
    assert err is None
    assert second is not None
    assert second["din"] == "02245669"  # left-padded
    assert second["brand_name"] == "APO-SALBUTAMOL HFA"
    assert second["tier3"] is True


def test_validate_record_quarantines_bad_id() -> None:
    row, err, detail = validate_record({"din": "02371234"})
    assert row is None
    assert err == "missing_report_id"
    assert detail


def test_normalize_din_rejects_ambiguous_values() -> None:
    assert _normalize_din("02371234") == "02371234"
    assert _normalize_din("2245669") == "02245669"
    assert _normalize_din("ABC12345") is None
    assert _normalize_din("12-345-678") is None  # do not strip junk into a fake DIN
    assert _normalize_din("123") is None
    row, err, _ = validate_record({"id": 1, "din": "12-345-678"})
    assert row is None
    assert err == "invalid_din"


def test_search_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ingestion.shortage_client.time.sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, headers={"auth-token": "tok-123"}, json={"user": {}})
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"en": "rate limit"}})
        return httpx.Response(200, json=_fixture_page())

    with ShortageClient(
        email="a@b.com", password="secret", base_url="https://example.test/api/v1"
    ) as client:
        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        page = client.search_page(limit=10)
        assert len(page["data"]) == 2
    assert calls["n"] == 2


def test_iter_search_paginates_and_returns_request_params() -> None:
    pages = [
        {
            "total": 3,
            "limit": 2,
            "offset": 0,
            "remaining": 1,
            "data": [{"id": 1}, {"id": 2}],
        },
        {
            "total": 3,
            "limit": 2,
            "offset": 2,
            "remaining": 0,
            "data": [{"id": 3}],
        },
    ]
    seen_offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, headers={"auth-token": "tok"}, json={"user": {}})
        offset = request.url.params.get("offset", "0")
        seen_offsets.append(offset)
        return httpx.Response(200, json=pages[int(offset) // 2])

    with ShortageClient(
        email="a@b.com", password="secret", base_url="https://example.test/api/v1"
    ) as client:
        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        results = list(client.iter_search(limit=2, type="shortage"))

    assert seen_offsets == ["0", "2"]
    assert results[0][0]["offset"] == 0
    assert results[0][0]["limit"] == 2
    assert results[1][0]["offset"] == 2
    assert len(results[0][1]["data"]) == 2
    assert len(results[1][1]["data"]) == 1


def test_validate_search_payload_rejects_non_list_data() -> None:
    with pytest.raises(ShortageAPIError, match="data"):
        validate_search_payload({"data": {"id": 1}, "remaining": 0})
    with pytest.raises(ShortageAPIError, match="remaining"):
        validate_search_payload({"data": [], "remaining": "nope"})


def test_is_newer_or_equal_freshness() -> None:
    older = datetime(2024, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert is_newer_or_equal(newer, older) is True
    assert is_newer_or_equal(older, newer) is False
    assert is_newer_or_equal(None, newer) is False
    assert is_newer_or_equal(newer, None) is True


def test_failed_ingest_marks_run_failed() -> None:
    """Failure finalize must happen even when the write transaction rolls back."""
    engine = MagicMock()
    start_conn = MagicMock()
    start_conn.__enter__.return_value = start_conn
    start_conn.__exit__.return_value = None
    start_conn.execute.return_value.scalar_one.return_value = 42

    work_conn = MagicMock()
    work_conn.__enter__.return_value = work_conn
    work_conn.__exit__.return_value = None

    finish_conn = MagicMock()
    finish_conn.__enter__.return_value = finish_conn
    finish_conn.__exit__.return_value = None

    # begin() used for: start run, work txn, finish failed
    engine.begin.side_effect = [start_conn, work_conn, finish_conn]

    client = MagicMock()
    client.iter_search.side_effect = ShortageAPIError("boom")

    with pytest.raises(ShortageAPIError, match="boom"):
        run_ingest(engine=engine, client=client, limit=10, max_pages=1)

    finish_sql = str(finish_conn.execute.call_args.args[0])
    finish_params = finish_conn.execute.call_args.args[1]
    assert "UPDATE ingestion_run" in finish_sql
    assert finish_params["state"] == "failed"
    assert finish_params["run_id"] == 42
    assert finish_params["rows_in"] == 0
    assert finish_params["rows_ok"] == 0
    assert finish_params["rows_quarantined"] == 0
    assert "not committed" in finish_params["error"]
    assert "boom" in finish_params["error"]
