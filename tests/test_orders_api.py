"""Supplier orders through the platform API and the daily run. Real Postgres, no model.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
The order agent runs as step 6 of each simulated day, once a supplier catalog is loaded.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
os.environ.setdefault("API_NO_AUTOSTART", "1")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from services.agents.daily_run import run_day  # noqa: E402
from services.agents.order_db import load_catalog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL")
NOV15, NOV16, NOV17 = date(2026, 11, 15), date(2026, 11, 16), date(2026, 11, 17)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _no_bedrock(*args, **kwargs):
    raise AssertionError("A test tried to call Bedrock")


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.setattr("services.agents.agent.run_agent", _no_bedrock)
    engine = create_engine(URL)
    yield engine
    engine.dispose()


@pytest.fixture
def api(engine):
    """Day 1 ingests the drugs; then the catalog is loaded; day 2's evening run drafts reorders."""

    first = run_day(engine, engine, NOV15, inbox={}, max_plans=0)
    assert first["orders"] == {"skipped": "no supplier catalog loaded"}
    load_catalog(engine)
    second = run_day(engine, engine, NOV16, inbox={}, max_plans=0)
    assert second["orders"]["drafted"] > 0
    client = TestClient(create_app(engine, engine))
    client.engine = engine
    return client


def pending(api, day="2026-11-16"):
    page = api.get("/api/orders", params={"as_of": day}).json()
    return [o for o in page["orders"] if o["status"] == "pending_approval"]


def test_no_catalog_means_an_empty_page_not_an_error(engine):
    run_day(engine, engine, NOV15, inbox={}, max_plans=0)
    response = TestClient(create_app(engine, engine)).get("/api/orders")
    assert response.status_code == 200
    assert response.json()["catalog"] is False and response.json()["orders"] == []


def test_the_page_lists_drafts_with_days_left_and_their_timeline(api):
    page = api.get("/api/orders", params={"as_of": "2026-11-16"}).json()
    assert page["catalog"] is True and page["mode"] == "database" and page["asOf"] == "2026-11-16"
    drafts = pending(api)
    assert drafts and all(o["kind"] == "routine" and o["supplierName"] for o in drafts)
    assert all(o["daysLeft"] is None or o["daysLeft"] >= 0 for o in drafts)
    assert {e["action"] for e in page["events"]} >= {"drafted"}


def test_people_decide_with_the_right_role_and_a_note_to_reject(api):
    first, second = pending(api)[:2]
    body = {"by": "Marc Tremblay", "role": "procurement", "as_of": "2026-11-16"}

    assert api.post(f"/api/orders/{first['id']}/approve", json={**body, "role": "pharmacist"}).status_code == 403
    assert api.post(f"/api/orders/{second['id']}/reject", json=body).status_code == 422
    assert api.post("/api/orders/PO-missing/approve", json=body).status_code == 404
    assert api.post(f"/api/orders/{first['id']}/cancel", json=body).status_code == 404

    assert api.post(f"/api/orders/{first['id']}/approve", json={**body, "by": "Dana Okafor", "role": "supply"}).json() == \
        {"order_id": first["id"], "status": "approved"}
    assert api.post(f"/api/orders/{second['id']}/reject", json={**body, "note": "Not needed"}).json() == \
        {"order_id": second["id"], "status": "cancelled"}
    again = api.post(f"/api/orders/{first['id']}/approve", json=body)
    assert again.status_code == 409 and again.json()["detail"]

    with api.engine.connect() as conn:
        humans = conn.execute(text(
            "SELECT actor, action, at::date FROM audit_log WHERE actor_type = 'human' ORDER BY audit_id")).all()
    assert [tuple(h) for h in humans] == [("Dana Okafor", "Order approved", NOV16),
                                          ("Marc Tremblay", "Order rejected", NOV16)]


def test_next_days_run_places_the_approved_order(api):
    order = pending(api)[0]
    api.post(f"/api/orders/{order['id']}/approve",
             json={"by": "Marc Tremblay", "role": "procurement", "as_of": "2026-11-16"})

    summary = run_day(api.engine, api.engine, NOV17, inbox={}, max_plans=0)

    assert summary["orders"]["decisions"].get("place", 0) >= 1
    page = api.get("/api/orders", params={"as_of": "2026-11-17"}).json()
    placed = next(o for o in page["orders"] if o["id"] == order["id"])
    assert placed["status"] in ("placed", "confirmed", "in_transit")
    assert any(e["order"] == order["id"] and e["action"] == "place" and e["sentVia"] == "simulated"
               for e in page["events"])
