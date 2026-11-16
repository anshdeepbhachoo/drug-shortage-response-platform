"""Database tests for the DPD loader (mocked API, real Postgres).

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
Every test rebuilds the schema with Alembic, so never point this at a real DB.

  TEST_DATABASE_URL=postgresql+psycopg://platform_owner:pw@127.0.0.1:55432/shortage_platform
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from ingestion.dpd_client import DpdClient  # noqa: E402
from ingestion.dpd_ingest import run_ingest as run_dpd_ingest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def engine(monkeypatch):
    """A freshly migrated schema for every test."""

    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    yield engine
    engine.dispose()


def count(engine, table, where="TRUE"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one()


# ---------------------------------------------------------
# DPD ingest (mocked API)
# ---------------------------------------------------------

def dpd_handler(fail_route=False):
    responses = {
        "drugproduct": [{"drug_code": 501, "drug_identification_number": "02242974",
                         "brand_name": "VASOPRESSIN INJECTION", "company_name": "EXAMPLE", "class_name": "Human",
                         "number_of_ais": "1", "last_update_date": "2025-01-31"}],
        "activeingredient": [{"drug_code": 501, "ingredient_name": "VASOPRESSIN", "strength": "20",
                              "strength_unit": "UNIT", "dosage_value": "1", "dosage_unit": "ML"},
                             {"drug_code": 501, "ingredient_name": "BAD", "strength": "-1"}],
        "status": {"drug_code": 501, "status": "MARKETED", "external_status_code": 2},
        "form": [{"drug_code": 501, "pharmaceutical_form_code": 33, "pharmaceutical_form_name": "SOLUTION"}],
        "route": [{"drug_code": 501, "route_of_administration_code": 1, "route_of_administration_name": "INTRAVENOUS"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.strip("/").split("/")[-1]
        if endpoint == "drugproduct" and request.url.params.get("din") != "02242974":
            return httpx.Response(200, json=[])
        if endpoint == "route" and fail_route:
            return httpx.Response(500)
        return httpx.Response(200, json=responses[endpoint])

    return handler


def dpd_client(handler):
    client = DpdClient(base_url="https://example.test/api/drug", sleep=lambda _: None)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_dpd_ingest_fills_product_and_children(engine):
    stats = run_dpd_ingest(engine, dpd_client(dpd_handler()), dins=["02242974", "00000001"])

    assert count(engine, "drug_product", "din = '02242974'") == 1
    assert count(engine, "active_ingredient") == 1
    assert count(engine, "product_status", "status_code = 2 AND is_current") == 1
    assert count(engine, "dosage_form") == 1 and count(engine, "route") == 1
    assert count(engine, "enrichment_queue", "state = 'done'") == 4
    assert count(engine, "quarantine", "error_code = 'STRENGTH_NEGATIVE'") == 1
    assert count(engine, "raw_snapshot", "source = 'dpd'") == 6   # 2 product lookups + 4 detail calls
    assert stats["queue_failures"] == 0
    assert count(engine, "ingestion_run", "source = 'dpd' AND state = 'succeeded'") == 1


def test_dpd_ingest_is_safe_to_rerun(engine):
    run_dpd_ingest(engine, dpd_client(dpd_handler()), dins=["02242974"])
    run_dpd_ingest(engine, dpd_client(dpd_handler()), dins=["02242974"])

    assert count(engine, "drug_product") == 1
    assert count(engine, "active_ingredient") == 1
    assert count(engine, "product_status") == 1


def test_dpd_failed_endpoint_is_queued_for_retry(engine):
    stats = run_dpd_ingest(engine, dpd_client(dpd_handler(fail_route=True)), dins=["02242974"])

    assert stats["queue_failures"] == 1
    with engine.connect() as conn:
        state, attempts, error = conn.execute(text(
            "SELECT state, attempts, last_error FROM enrichment_queue WHERE endpoint = 'route'")).one()
    assert (state, attempts) == ("pending", 1)
    assert "HTTP 500" in error
    assert count(engine, "enrichment_queue", "state = 'done'") == 3
