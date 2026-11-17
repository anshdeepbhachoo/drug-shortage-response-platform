"""Order agent on the database. Real Postgres, offline rules (no model).

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).

Baseline fixture (stock snapshot Nov 15): cyclophosphamide 500 mg at A has 60
vials, uses 10/day, and Meridian's 300 (O-1001, Nov 18) is 3 weeks late per
mock/emails/clear.txt -> A is short 180. The plan gets an emergency order of
180 from Apex Sterile (3 days, $8,920.80), which procurement may approve.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from ingestion.synthetic_loader import load_folder  # noqa: E402
from services.agents.agent import process_email  # noqa: E402
from services.agents.approvals import ApprovalError, approve_plan  # noqa: E402
from services.agents.db_data import PostgresData  # noqa: E402
from services.agents.order_agent import OrderError, SimulatedSupplierChannel  # noqa: E402
from services.agents.order_db import (  # noqa: E402
    approve_order,
    load_catalog,
    load_catalog_from_db,
    managed_orders,
    reject_order,
    run_orders,
    scan,
)
from services.agents.results_store import save_pipeline_result  # noqa: E402
from tests.test_agent import FakeModel, assessment  # noqa: E402
from tests.test_followup_db import PLAN  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
URL = os.environ.get("TEST_DATABASE_URL")
NOV15 = date(2026, 11, 15)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    load_folder(engine, BASELINE)
    load_catalog(engine, MOCK)
    yield engine
    engine.dispose()


@pytest.fixture
def data(engine):
    return PostgresData(engine, as_of=NOV15)


def shortage_plan(engine, data) -> tuple[str, int]:
    """Plan for the cyclophosphamide delay. Returns (plan_id, order option number)."""

    model = FakeModel(assessments=[assessment()], plans=[PLAN])
    result = process_email((MOCK / "emails" / "clear.txt").read_text(), data=data, llm=model, today=NOV15)
    assert result["status"] == "plan_generated"
    option = next(i for i, o in enumerate(result["proposed_options"], start=1) if o["type"] == "order")
    plan_id = save_pipeline_result(engine, result, source_ref="mock/emails/clear.txt")["plan_id"]
    return plan_id, option


def rows(engine, sql, **params):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def test_catalog_is_loaded_for_tracked_drugs_only(engine):
    catalog = load_catalog_from_db(engine)
    assert catalog is not None
    assert {o.supplier_id for o in catalog.offers("99000001")} == {"meridian-pharma", "apex-sterile", "cascade-oncology"}
    assert catalog.offers("99000021") == []   # methotrexate is not in the baseline fixture


def test_plan_stores_order_lines_with_supplier(engine, data):
    plan_id, option = shortage_plan(engine, data)
    [line] = rows(engine, "SELECT * FROM plan_action WHERE plan_id = :p AND type = 'order'", p=plan_id)
    assert (line["supplier_id"], line["to_site"], line["units"], line["option_index"]) == ("apex-sterile", "A", 180, option)
    assert line["from_site"] is None and line["verified"] is True


def test_approved_order_option_becomes_a_placed_purchase_order(engine, data):
    plan_id, option = shortage_plan(engine, data)
    with pytest.raises(ApprovalError):
        approve_plan(engine, plan_id, "Priya Nair", "pharmacist", data, option=option)  # clinical role, not orders
    assert approve_plan(engine, plan_id, "Sam Lee", "procurement", data, option=option)["approved"]

    actions, _ = run_orders(engine, data, datetime(2026, 11, 15, 10))
    [placed] = actions
    assert placed.decision == "place" and placed.sent_via == "simulated"
    [order] = rows(engine, "SELECT * FROM purchase_order WHERE kind = 'shortage'")
    assert order["status"] == "placed" and order["supplier_id"] == "apex-sterile" and order["qty"] == 180
    assert order["plan_id"] == plan_id and order["action_id"].startswith(plan_id)
    assert order["expected_date"] == date(2026, 11, 18)

    # Running again does not create a second order for the same plan action.
    run_orders(engine, data, datetime(2026, 11, 15, 11))
    assert len(rows(engine, "SELECT 1 FROM purchase_order WHERE kind = 'shortage'")) == 1


def test_supplier_replies_and_state_carry_across_runs(engine, data):
    plan_id, option = shortage_plan(engine, data)
    approve_plan(engine, plan_id, "Sam Lee", "procurement", data, option=option)
    run_orders(engine, data, datetime(2026, 11, 15, 10))                    # placed

    channel = SimulatedSupplierChannel.from_file(MOCK / "orders" / "supplier_replies.json")
    [confirmed], _ = run_orders(engine, data, datetime(2026, 11, 15, 14), channel)
    assert confirmed.decision == "accept"
    [shipped], _ = run_orders(engine, data, datetime(2026, 11, 17, 12), channel)
    assert shipped.decision == "accept"
    [delivered], _ = run_orders(engine, data, datetime(2026, 11, 18, 12), channel)
    assert delivered.decision == "close"
    [order] = rows(engine, "SELECT status, received_qty, delivered_at FROM purchase_order WHERE kind = 'shortage'")
    assert order == {"status": "delivered", "received_qty": 180, "delivered_at": date(2026, 11, 18)}

    log = rows(engine, "SELECT action, actor_type FROM audit_log WHERE actor = 'order-agent' ORDER BY audit_id")
    assert [entry["action"] for entry in log] == [
        "Order created from approved plan", "Order: place", "Order: accept", "Order: accept", "Order: close",
    ]


def test_silent_supplier_reminders_survive_restarts(engine, data):
    plan_id, option = shortage_plan(engine, data)
    approve_plan(engine, plan_id, "Sam Lee", "procurement", data, option=option)
    run_orders(engine, data, datetime(2026, 11, 15, 10))
    assert run_orders(engine, data, datetime(2026, 11, 16, 12))[0][0].decision == "chase"
    [order] = managed_orders(engine)
    assert order.chases == 1 and order.last_chased_at == datetime(2026, 11, 16, 12)


def test_routine_scan_then_people_decide(engine, data):
    drafted = scan(engine, data, datetime(2026, 11, 15, 8))
    assert drafted and all(o.status == "pending_approval" for o in drafted)
    assert scan(engine, data, datetime(2026, 11, 15, 9)) == []            # nothing drafted twice

    first, second = drafted[0], drafted[1]
    with pytest.raises(OrderError):
        approve_order(engine, first.order_id, "Priya Nair", "pharmacist", data, datetime(2026, 11, 15, 9))
    approve_order(engine, first.order_id, "Sam Lee", "procurement", data, datetime(2026, 11, 15, 9))
    reject_order(engine, second.order_id, "Sam Lee", "procurement", "Not needed", data, datetime(2026, 11, 15, 9))

    status = {r["order_id"]: r["status"] for r in rows(engine, "SELECT order_id, status FROM purchase_order WHERE kind = 'routine'")}
    assert status[first.order_id] == "approved" and status[second.order_id] == "cancelled"
    humans = rows(engine, "SELECT actor, action FROM audit_log WHERE actor_type = 'human' ORDER BY audit_id")
    assert humans == [{"actor": "Sam Lee", "action": "Order approved"}, {"actor": "Sam Lee", "action": "Order rejected"}]


def test_ui_state_shows_the_order_option_as_a_supplier_order(engine, data):
    from apps.api.state import build_state

    plan_id, option = shortage_plan(engine, data)
    cards = [c for c in build_state(engine, NOV15)["plans"] if c["id"] == f"{plan_id}-O{option}"]
    [card] = cards
    assert card["transfers"] == []
    assert card["orders"] == [{"qty": 180, "etaDay": 3, "supplier": "Apex Sterile", "premiumPct": 0}]
    assert card["cost"] == "≈ $8,921 (supplier order)" and "A" in card["sites"]
