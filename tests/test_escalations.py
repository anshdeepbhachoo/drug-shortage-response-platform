"""Escalations and notifications: work goes to the role that acts on it, and shows on that role's overview.

The database tests are skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

import pytest

from services.store.escalations import EscalationError, target_for


def test_an_order_goes_to_procurement_and_procurement_goes_back_to_the_planner():
    assert target_for("order", "supply_manager") == "procurement"
    assert target_for("order", "pharmacist") == "procurement"
    assert target_for("order", "procurement") == "supply_manager"
    assert target_for("transfer", "pharmacist") == "supply_manager"
    with pytest.raises(EscalationError):
        target_for("order", "auditor")


def test_the_order_agent_escalates_to_whoever_can_act():
    from services.agents.order_agent import escalation_role

    base = {"margin_days": 3.0, "cancelled": False, "supplier_reply": None}
    assert escalation_role(base | {"supplier_reply": "question"}) == "procurement"        # answer the supplier
    assert escalation_role(base | {"supplier_reply": "partial"}) == "supply_manager"      # cover the missing units
    assert escalation_role(base | {"margin_days": -1.0}) == "supply_manager"              # runs out first: new plan


from tests.test_daily_run import URL, engine, rows  # noqa: E402,F401  (engine is a fixture)
from tests.test_order_orchestrator import CISPLATIN, EPINEPHRINE, approve, client, scenario  # noqa: E402,F401

db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def mine(engine, role):
    state = client(engine).get("/api/state", params={"as_of": "2026-11-15"}).json()
    return [n for n in state["notifications"] if n["toRole"] == role]


@db
def test_dana_escalates_the_order_she_approved_to_procurement_only(engine, scenario):
    [order_id, *_] = approve(engine, EPINEPHRINE, "transfer_order", "supply_manager")
    api = client(engine)

    sent = api.post(f"/api/escalations/{order_id}", json={"by": "Dana Okafor", "role": "supply",
                                                         "message": "Please confirm SAP sent it today.", "as_of": "2026-11-15"})
    assert sent.json()["to_role"] == "procurement"
    [note] = [n for n in mine(engine, "procurement") if n["from"] == "Dana Okafor"]
    assert (note["title"], note["body"], note["objectId"], note["read"]) == (
        "Escalated to you", "Please confirm SAP sent it today.", order_id, False)
    assert not [n for n in mine(engine, "supply") if n["from"] == "Dana Okafor"]        # not to herself

    assert api.post(f"/api/escalations/{order_id}", json={"by": "Jordan Lee", "role": "auditor",
                                                         "message": "x"}).status_code == 409
    back = api.post(f"/api/escalations/{order_id}", json={"by": "Marc Tremblay", "role": "procurement",
                                                         "message": "Supplier can't ship until Monday."})
    assert back.json()["to_role"] == "supply_manager"
    assert [n["from"] for n in mine(engine, "supply")] == ["Marc Tremblay"]

    api.post(f"/api/notifications/{note['id']}/read", json={"by": "Marc Tremblay", "role": "procurement"})
    assert [n["read"] for n in mine(engine, "procurement") if n["id"] == note["id"]] == [True]


@db
def test_an_order_waiting_on_procurement_notifies_them_until_it_is_placed(engine, scenario):
    order_ids = approve(engine, CISPLATIN, "order", "procurement")          # portal credit hold -> email for procurement

    waiting = [n for n in mine(engine, "procurement") if n["from"] == "Order orchestrator"]
    assert {n["objectId"] for n in waiting} == set(order_ids)
    assert all(n["title"] == "Order email ready to send" and "credit hold" in n["body"] for n in waiting)

    api = client(engine)
    for order_id in order_ids:
        api.post(f"/api/orders/{order_id}/status", json={"by": "Marc Tremblay", "role": "procurement", "status": "placed"})
    assert not [n for n in mine(engine, "procurement") if n["from"] == "Order orchestrator"]
