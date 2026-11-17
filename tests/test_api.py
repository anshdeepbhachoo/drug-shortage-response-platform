"""HTTP API for the web UI. Real Postgres, fake model.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
os.environ.setdefault("API_NO_AUTOSTART", "1")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from ingestion.synthetic_loader import load_progression  # noqa: E402
from tests.test_agent import FakeModel, assessment, transfer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DAYS = ROOT / "scenarios" / "progression" / "days"
URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

# Keys each record must have, from apps/web/src/lib/types.ts.
SHAPES = {
    "sites": {"id", "name", "short"},
    "drugs": {"id", "name", "form", "unit", "criticalList", "dpdRef"},
    "stock": {"siteId", "drugId", "onHand", "dailyUsage", "safetyDays", "orders", "inventoryRef", "usageRef"},
    "events": {"id", "drugId", "kind", "title", "summary", "source", "sourceRef", "confidence", "atMin", "status"},
    "plans": {"id", "eventId", "drugId", "solverRun", "rank", "title", "transfers", "orders", "score", "cost",
              "leadTime", "explanation", "citations", "status"},
    "emails": {"id", "from", "subject", "atMin", "body", "outcome", "extraction"},
    "audit": {"id", "atMin", "actor", "actorName", "action", "object", "detail", "refs"},
}


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    load_progression(engine, DAYS)
    yield engine
    engine.dispose()


def client(engine, model=None):
    return TestClient(create_app(engine, engine, llm=model))


def run_email(api, name="clear.txt"):
    response = api.post("/api/agent/run", json={"email": name, "as_of": "2026-11-15"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def api_with_plan(engine):
    api = client(engine, FakeModel(assessments=[assessment()], plans=[transfer("B", 180)]))
    result = run_email(api)
    assert result["status"] == "plan_generated" and result["shortfall"] == {"A": 180}
    return api, result["saved"]["plan_id"]


# ---------------------------------------------------------
# Reading
# ---------------------------------------------------------

def test_state_has_every_shape_the_ui_needs(api_with_plan):
    api, plan_id = api_with_plan

    state = api.get("/api/state", params={"as_of": "2026-11-15"}).json()

    assert state["asOf"] == "2026-11-15" and len(state["days"]) == 10
    assert len(state["sites"]) == 6 and len(state["drugs"]) == 22 and len(state["stock"]) == 132
    for key, fields in SHAPES.items():
        assert state[key], f"{key} is empty"
        for record in state[key]:
            assert fields <= set(record), f"{key} record missing {fields - set(record)}"


def test_stock_and_orders_follow_the_chosen_day(engine):
    api = client(engine)
    a_on = lambda day: next(l for l in api.get("/api/state", params={"as_of": day}).json()["stock"]
                            if l["siteId"] == "A" and l["drugId"] == "99000001")

    nov15, nov19 = a_on("2026-11-15"), a_on("2026-11-19")
    assert nov15["onHand"] == 60 and nov19["onHand"] == 120
    assert nov15["orders"][0] == {"id": "O-1001", "qty": 300, "etaDay": 3, "supplier": "Meridian Pharma", "status": "on-time"}
    late = a_on("2026-11-16")["orders"][0]
    assert late["status"] == "delayed" and late["etaDay"] == 21 and late["originalEtaDay"] == 2


def test_plan_options_become_cards_and_email_is_shown(api_with_plan):
    api, plan_id = api_with_plan
    state = api.get("/api/state").json()

    [card] = [p for p in state["plans"] if p["solverRun"] == plan_id]
    assert card["id"] == f"{plan_id}-O1" and card["status"] == "pending-approval"
    assert card["transfers"] == [{"from": "B", "to": "A", "qty": 180}]
    [event] = state["events"]
    assert event["kind"] == "delay" and event["source"] == "supplier-email" and event["resupplyDay"] == 24
    [email] = state["emails"]
    assert email["outcome"] == "accepted" and "Cyclophosphamide" in email["subject"]


def test_hostile_email_shows_as_quarantined(engine):
    api = client(engine, FakeModel())
    assert run_email(api, "hostile.txt")["status"] == "needs_review"

    [email] = api.get("/api/state").json()["emails"]
    assert email["outcome"] == "quarantined"
    assert any("instruction override" in flag for flag in email["extraction"]["flags"])


# ---------------------------------------------------------
# Deciding
# ---------------------------------------------------------

def test_supply_manager_approves_from_the_ui(api_with_plan):
    api, plan_id = api_with_plan

    response = api.post(f"/api/plans/{plan_id}-O1/approve",
                        json={"by": "Dana Okafor", "role": "supply", "note": "OK", "as_of": "2026-11-15"})

    assert response.status_code == 200 and response.json()["status"] == "approved"
    state = api.get("/api/state").json()
    assert next(p for p in state["plans"] if p["solverRun"] == plan_id)["status"] == "approved"
    approved = next(a for a in state["audit"] if a["action"] == "Approved")
    assert approved["actor"] == "supply" and approved["actorName"] == "Dana Okafor"
    assert approved["detail"] == 'Option 1 (transfer): 180 units from Northside to Central General. Note: "OK"'


def test_role_rules_come_back_as_messages(api_with_plan):
    api, plan_id = api_with_plan

    response = api.post(f"/api/plans/{plan_id}-O1/approve", json={"by": "Priya Nair", "role": "pharmacist"})
    assert response.status_code == 409 and "cannot approve a transfer" in response.json()["detail"]

    response = api.post(f"/api/plans/{plan_id}-O1/approve", json={"by": "Jordan Lee", "role": "auditor"})
    assert response.status_code == 403


def test_reject_needs_a_reason(api_with_plan):
    api, plan_id = api_with_plan

    assert api.post(f"/api/plans/{plan_id}-O1/reject", json={"by": "Priya Nair", "role": "pharmacist"}).status_code == 409
    response = api.post(f"/api/plans/{plan_id}-O1/reject", json={"by": "Priya Nair", "role": "pharmacist", "note": "Wait"})
    assert response.status_code == 200 and response.json()["status"] == "rejected"


def test_followup_check_runs_after_approval(api_with_plan):
    api, plan_id = api_with_plan
    api.post(f"/api/plans/{plan_id}-O1/approve", json={"by": "Dana Okafor", "role": "supply", "as_of": "2026-11-15"})

    actions = api.post("/api/followup/run", json={"now": "2026-11-15T09:00:00", "as_of": "2026-11-15"}).json()

    assert [a["decision"] for a in actions] == ["wait"]
    assert any(a["action"] == "Follow-up: wait" for a in api.get("/api/state").json()["audit"])


def test_unknown_email_is_404(engine):
    assert client(engine).post("/api/agent/run", json={"email": "../../.env"}).status_code == 404


def test_same_email_is_not_planned_twice_while_open(api_with_plan):
    api, plan_id = api_with_plan

    again = api.post("/api/agent/run", json={"email": "clear.txt", "as_of": "2026-11-15"})
    assert again.status_code == 409 and "already being handled" in again.json()["detail"]

    api.post(f"/api/plans/{plan_id}-O1/approve", json={"by": "Dana Okafor", "role": "supply", "as_of": "2026-11-15"})
    state = api.get("/api/state").json()
    assert [e["status"] for e in state["events"]] == ["mitigated"]       # handled once approved


def test_simulation_can_start_with_no_hospital_data(monkeypatch):
    """Fresh database, nothing ingested: the UI still loads, and running a day brings the data in."""

    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    engine = create_engine(URL)
    api = client(engine)

    before = api.get("/api/state").json()
    assert len(before["days"]) == 10 and before["loadedDays"] == [] and before["stock"] == []

    assert api.post("/api/day/run", json={"day": "2026-11-15", "planner": "rules"}).status_code == 200

    after = api.get("/api/state", params={"as_of": "2026-11-15"}).json()
    assert after["loadedDays"] == ["2026-11-15"] and len(after["stock"]) == 132
    engine.dispose()
