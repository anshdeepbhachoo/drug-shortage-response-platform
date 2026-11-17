"""Database tests for the DPD loader and nightly workflow (mocked APIs, real Postgres).

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


# ---------------------------------------------------------
# Shortage-driven loading and the nightly workflow
# ---------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402
import json  # noqa: E402

from ingestion.dpd_ingest import pick_from_shortages, run_from_shortages  # noqa: E402
from ingestion.workflow import nightly_tasks, run_workflow  # noqa: E402

# DPD catalogue for the fake API: din -> drug_code
CATALOGUE = {"02371234": 66031, "02245669": 12345, "00693820": 8604, "00000077": 77}


def catalogue_handler(calls=None):
    by_code = {code: din for din, code in CATALOGUE.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.strip("/").split("/")[-1]
        params = request.url.params
        if calls is not None:
            calls.append((endpoint, dict(params)))
        if endpoint == "drugproduct":
            din = params.get("din") or by_code.get(int(params.get("id", 0)))
            if din not in CATALOGUE:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"drug_code": CATALOGUE[din], "drug_identification_number": din,
                                              "brand_name": f"DRUG {din}"}])
        code = int(params["id"])
        return httpx.Response(200, json={
            "activeingredient": [{"drug_code": code, "ingredient_name": "X", "strength": "1", "strength_unit": "MG"}],
            "status": {"drug_code": code, "external_status_code": 2},
            "form": [{"drug_code": code, "pharmaceutical_form_code": 1, "pharmaceutical_form_name": "SOLUTION"}],
            "route": [{"drug_code": code, "route_of_administration_code": 1, "route_of_administration_name": "ORAL"}],
        }[endpoint])

    return handler


def add_shortages(engine, *reports):
    """reports: (report_id, din, drug_code, status, tier3, updated 'YYYY-MM-DD')."""

    with engine.begin() as conn:
        run_id = conn.execute(text("INSERT INTO ingestion_run (source, state) VALUES ('test', 'running') RETURNING run_id")).scalar_one()
        snapshot = conn.execute(text("""INSERT INTO raw_snapshot (run_id, source, endpoint, request_params, payload, payload_sha256)
                                        VALUES (:r, 'test', '/search', '{}', '{}', repeat('0', 64)) RETURNING snapshot_id"""),
                                {"r": run_id}).scalar_one()
        for report_id, din, drug_code, status, tier3, updated in reports:
            conn.execute(text("""INSERT INTO shortage_report (report_id, din, drug_code, status, tier3, updated_date, snapshot_id)
                                 VALUES (:id, :din, :code, :status, :tier3, :updated, :snap)"""),
                         {"id": report_id, "din": din, "code": drug_code, "status": status, "tier3": tier3,
                          "updated": updated, "snap": snapshot})


def test_only_active_shortages_are_picked_tier3_first(engine):
    add_shortages(engine,
                  (1, "02371234", 66031, "active_confirmed", False, "2026-11-10"),
                  (2, "00693820", 8604, "active_confirmed", True, "2026-11-01"),
                  (3, "02245669", 12345, "resolved", True, "2026-11-12"))

    dins, codes = pick_from_shortages(engine, limit=10)

    assert dins == ["00693820", "02371234"]      # Tier 3 first; resolved left out
    assert codes == []
    assert pick_from_shortages(engine, limit=10, statuses=None)[0][0] in {"00693820", "02245669"}


def test_drugs_already_loaded_are_skipped_until_stale(engine):
    add_shortages(engine, (1, "00693820", 8604, "active_confirmed", True, "2026-11-01"))
    run_from_shortages(engine, dpd_client(catalogue_handler()))

    assert pick_from_shortages(engine, limit=10) == ([], [])
    with engine.begin() as conn:
        conn.execute(text("UPDATE drug_product SET last_seen_at = now() - interval '8 days'"))
    assert pick_from_shortages(engine, limit=10) == (["00693820"], [])


def test_batches_are_separate_runs_and_only_shortage_drugs_are_fetched(engine):
    add_shortages(engine,
                  (1, "02371234", 66031, "active_confirmed", False, "2026-11-10"),
                  (2, "00693820", 8604, "active_confirmed", True, "2026-11-01"),
                  (3, None, 77, "active_confirmed", False, "2026-11-05"))   # no DIN: looked up by drug_code
    calls = []

    totals = run_from_shortages(engine, dpd_client(catalogue_handler(calls)), batch_size=2, max_batches=None)

    assert totals["batches"] == 2
    assert totals["drugs"] == 3
    assert totals["remaining"] == 0
    assert count(engine, "drug_product") == 3
    assert count(engine, "ingestion_run", "source = 'dpd'") == 2
    product_calls = [p for endpoint, p in calls if endpoint == "drugproduct"]
    assert {p.get("din") or p.get("id") for p in product_calls} == {"02371234", "00693820", "77"}
    assert "02245669" not in json.dumps(calls)           # never fetched: not in shortage


def test_drug_unknown_to_dpd_does_not_loop(engine):
    add_shortages(engine, (1, "09999999", None, "active_confirmed", False, "2026-11-01"))

    totals = run_from_shortages(engine, dpd_client(catalogue_handler()), batch_size=1, max_batches=None)

    assert totals["batches"] == 1
    assert totals["not_found"] == 1


class FakeShortageClient:
    """Stands in for ShortageClient: one page of the saved API fixture."""

    def __init__(self, fail=False):
        self.fail = fail

    def iter_search(self, limit=100, max_pages=None, **filters):
        if self.fail:
            raise RuntimeError("shortage API down")
        page = json.loads((ROOT / "tests" / "fixtures" / "shortage_search_page.json").read_text())
        yield {"limit": limit, **filters}, page


def test_nightly_workflow_runs_shortages_then_dpd(engine):
    tasks = nightly_tasks(shortage_client=FakeShortageClient(), dpd_client=dpd_client(catalogue_handler()))

    results = run_workflow("nightly", tasks, engine, {"batch_size": 50}, sleep=lambda _: None, log=lambda _: None)

    assert [(r.name, r.state) for r in results] == [("shortages", "succeeded"), ("dpd", "succeeded")]
    assert count(engine, "shortage_report") >= 1
    # Only the active shortage from the fixture is fetched from DPD.
    assert count(engine, "drug_product", "din = '02371234'") == 1
    assert count(engine, "drug_product", "din = '02245669'") == 0
    assert count(engine, "ingestion_run", "source = 'workflow:nightly' AND state = 'succeeded'") == 1


def test_nightly_workflow_skips_dpd_when_shortages_fail(engine):
    calls = []
    tasks = nightly_tasks(shortage_client=FakeShortageClient(fail=True), dpd_client=dpd_client(catalogue_handler(calls)))

    results = run_workflow("nightly", tasks, engine, sleep=lambda _: None, log=lambda _: None)

    assert [(r.name, r.state) for r in results] == [("shortages", "failed"), ("dpd", "skipped")]
    assert results[0].attempts == 3
    assert calls == []
    with engine.connect() as conn:
        state, error = conn.execute(text("SELECT state, error FROM ingestion_run WHERE source = 'workflow:nightly'")).one()
    assert state == "failed" and "shortage API down" in error
