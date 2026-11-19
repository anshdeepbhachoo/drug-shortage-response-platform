"""The order agent: drafts the supplier request and follows approved orders up every evening.

The unit tests need nothing. The pipeline tests are skipped unless TEST_DATABASE_URL points at a
disposable Postgres (owner role); they use the 10-day scenario, where fentanyl (99000013) gets an
order option from Apex Sterile for Central General on Nov 15.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from services.agents import order_agent
from services.agents.order_agent import (
    DECISION_SYSTEM_PROMPT,
    REQUEST_SYSTEM_PROMPT,
    OrderAgent,
    WatchedOrder,
    allowed_decisions,
    hard_rule,
    order_facts,
)

SNAPSHOT = datetime(2026, 11, 15)


@pytest.fixture(autouse=True)
def no_supplier_replies(monkeypatch):
    """These tests follow orders that suppliers never answer (supplier replies: tests/test_order_replies.py) and
    that people place by hand (the order orchestrator: tests/test_order_orchestrator.py)."""

    monkeypatch.setenv("ORDER_REPLY_STORY", "none")
    monkeypatch.setenv("ORDER_SUBMISSION", "off")


class Data:
    """Central General (A) has 60 units and uses 5 a day: 12 days."""

    def __init__(self, on_hand=60, daily_use=5):
        self.rows = [{"site_id": "A", "site": "Central General", "on_hand": on_hand, "daily_use": daily_use}]

    def stock(self, din):
        return self.rows

    def drug(self, din):
        return {"name": "Fentanyl 50 mcg/mL ampule"}


class Model:
    """Scripted replies per prompt, in order."""

    def __init__(self, requests=(), decisions=()):
        self.replies = {REQUEST_SYSTEM_PROMPT: list(requests), DECISION_SYSTEM_PROMPT: list(decisions)}
        self.calls = 0

    def __call__(self, system, task, registry):
        self.calls += 1
        reply = self.replies[system].pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)


def an_order(**changes):
    values = dict(order_id="PO-P-1-O2", plan_id="P-1", din="99000013", site_id="A", supplier_id="apex-sterile",
                  supplier="Apex Sterile", qty=40, status="approved", expected_date=date(2026, 11, 20),
                  approved_at=datetime(2026, 11, 15, 12), approver_role="procurement")
    return WatchedOrder(**(values | changes))


def facts(order=None, now=datetime(2026, 11, 15, 18), data=None, nudges=0, last_nudge=None):
    return order_facts(order or an_order(), (data or Data()).stock("x"), SNAPSHOT, now, nudges, last_nudge)


def check(order=None, now=datetime(2026, 11, 15, 18), data=None, llm=None, nudges=0, last_nudge=None):
    records = []
    agent = OrderAgent(data or Data(), SNAPSHOT, llm, write=lambda record, action: records.append((action, record)))
    return agent.check(order or an_order(), now, nudges, last_nudge), records


# ---------------------------------------------------------
# Facts and hard rules (code)
# ---------------------------------------------------------

def test_facts_compare_the_hospitals_stock_with_the_orders_arrival():
    f = facts(now=datetime(2026, 11, 16, 18))     # stock: 60 - 5 * 1.75 days = 51.25 -> 10.25 days
    assert f["site_cover_days"] == 10.25
    assert f["days_to_arrival"] == 4.25           # due by the end of Nov 20
    assert f["margin_days"] == 6.0
    assert f["unplaced_too_long"] is True         # approved 30 hours ago, not placed


@pytest.mark.parametrize("changes, data, decision", [
    ({"status": "delivered"}, Data(), "close"),
    ({"status": "cancelled"}, Data(), "replan"),
    ({"expected_date": date(2026, 11, 30)}, Data(), "replan"),           # arrives after the hospital runs out
    ({"expected_date": date(2026, 11, 30)}, Data(on_hand=10), "escalate"),  # ...and too soon to replan
])
def test_hard_rules_decide_the_clear_cases(changes, data, decision):
    assert hard_rule(facts(an_order(**changes), data=data))[0] == decision


def test_a_placed_order_arriving_in_time_is_on_track():
    action, records = check(an_order(status="placed"), now=datetime(2026, 11, 16, 18))
    assert (action.decision, action.decided_by) == ("wait", "rule")
    assert records[0][0] == "Order follow-up: wait"


def test_an_order_not_placed_after_a_day_nudges_procurement():
    action, _ = check(now=datetime(2026, 11, 16, 18))
    assert (action.decision, action.message_to) == ("nudge", "procurement")
    assert "not marked as placed" in action.message_draft
    assert action.execution_performed is False


def test_a_late_order_nudges_the_supplier_then_stops_nudging():
    late = an_order(status="placed", expected_date=date(2026, 11, 16))
    action, _ = check(late, now=datetime(2026, 11, 17, 18))
    assert (action.decision, action.message_to) == ("nudge", "Apex Sterile")

    f = facts(late, now=datetime(2026, 11, 18, 18), nudges=2, last_nudge=datetime(2026, 11, 17, 18))
    assert allowed_decisions(f) == {"replan", "escalate"}


# ---------------------------------------------------------
# The model: grey zones and wording, always checked
# ---------------------------------------------------------

def test_the_agent_chooses_in_a_grey_zone_and_its_choice_is_checked():
    late = an_order(status="placed", expected_date=date(2026, 11, 16))
    model = Model(decisions=[
        {"decision": "wait", "reason": "Fine.", "message_draft": None},              # not allowed when overdue
        {"decision": "nudge", "reason": "One day late; 7 days of margin.", "message_draft": "Please confirm a date."},
    ])
    action, _ = check(late, now=datetime(2026, 11, 17, 18), llm=model)
    assert (action.decision, action.decided_by, action.message_draft) == ("nudge", "agent", "Please confirm a date.")
    assert action.message_to == "Apex Sterile"   # code addresses it, not the model
    assert model.calls == 2


def test_rules_decide_when_the_agents_answers_fail_the_checks():
    late = an_order(status="placed", expected_date=date(2026, 11, 16))
    model = Model(decisions=[{"decision": "close", "reason": "x"}, {"decision": "close", "reason": "x"}])
    action, _ = check(late, now=datetime(2026, 11, 17, 18), llm=model)
    assert (action.decision, action.decided_by) == ("nudge", "fallback")


def test_the_request_draft_uses_the_agents_wording_when_it_states_the_order():
    good = {"subject": "Order PO-P-1-O2", "body": "Please supply 40 units of fentanyl to Central General by Nov 20."}
    record = OrderAgent(Data(), SNAPSHOT, Model(requests=[good])).draft_request(an_order(), datetime(2026, 11, 15, 12))
    assert (record["decided_by"], record["message_to"], record["subject"]) == ("agent", "Apex Sterile", "Order PO-P-1-O2")


def test_a_request_that_changes_the_quantity_falls_back_to_the_template():
    wrong = {"subject": "Order PO-P-1-O2", "body": "Please supply 400 ampules."}
    record = OrderAgent(Data(), SNAPSHOT, Model(requests=[wrong, wrong])).draft_request(an_order(), datetime(2026, 11, 15, 12))
    assert record["decided_by"] == "rule" and "template used" in record["note"]
    assert "40 units of Fentanyl" in record["message_draft"] and "PO-P-1-O2" in record["message_draft"]


def test_bedrock_is_used_when_configured_and_rules_only_when_asked(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model")
    assert order_agent.resolve_model(None) is not None
    assert order_agent.resolve_model(None, use_rules=True) is None
    monkeypatch.delenv("BEDROCK_MODEL_ID")
    assert order_agent.resolve_model(None) is None


# ---------------------------------------------------------
# In the pipeline (real Postgres, the 10-day scenario)
# ---------------------------------------------------------

from tests.test_daily_run import NOV15, NOV16, URL, engine, rows  # noqa: E402,F401  (engine is a fixture)

db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
FENTANYL = "99000013"
NOV17, NOV18 = date(2026, 11, 17), date(2026, 11, 18)


def client(engine):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    return TestClient(create_app(engine, engine))


def approved_order(engine):
    """Run Nov 15 on rules and approve fentanyl's order option as procurement. Returns (plan_id, order_id)."""

    from services.pipeline.daily_run import run_day

    run_day(engine, engine, NOV15, planner="rules", inbox={})
    [(plan_id, option)] = rows(engine, """
        SELECT p.plan_id, a.option_index FROM plan p JOIN supply_event e USING (event_id)
        JOIN plan_action a USING (plan_id)
        WHERE e.din = :din AND p.status = 'pending_approval' AND a.type = 'order'""", din=FENTANYL)
    result = client(engine).post(f"/api/plans/{plan_id}-O{option}/approve",
                                 json={"by": "Marc Tremblay", "role": "procurement", "as_of": "2026-11-15"}).json()
    assert result["approved"] and result["type"] == "order"
    return plan_id, result["purchase_orders"][0]


def tick(engine, day):
    from services.pipeline.graph.pipeline import AgentPipeline

    return AgentPipeline(engine, engine, use_rules=True).tick(FENTANYL, day, datetime.combine(day, datetime.min.time()).replace(hour=18))


def record(engine, order_id, status, role="procurement", day="2026-11-16"):
    return client(engine).post(f"/api/orders/{order_id}/status",
                               json={"by": "Marc Tremblay", "role": role, "status": status, "as_of": day})


@db
def test_approving_an_order_drafts_the_supplier_request_and_pauses_for_the_evening(engine):
    from services.pipeline.graph.pipeline import AgentPipeline

    plan_id, order_id = approved_order(engine)

    paused = AgentPipeline(engine, engine).paused(FENTANYL)
    assert (paused["waiting_for"], paused["plan_id"]) == ("order_follow_up", plan_id)
    [(action, to)] = rows(engine, "SELECT action, detail->>'message_to' FROM audit_log WHERE actor = 'order-agent'")
    assert (action, to) == ("Order request drafted", "Apex Sterile")


@db
def test_the_daily_run_follows_the_order_up_and_nudges_procurement_to_place_it(engine):
    from services.pipeline.daily_run import run_day

    _, order_id = approved_order(engine)

    day2 = run_day(engine, engine, NOV16, planner="rules", inbox={})

    assert {"transfer": order_id, "decision": "nudge", "kind": "order"} in day2["followup"]
    state = client(engine).get("/api/state", params={"as_of": "2026-11-17"}).json()
    [mine] = [f for f in state["followups"] if f["id"] == order_id]
    assert (mine["kind"], mine["orderStatus"], mine["decision"], mine["messageTo"]) == ("order", "approved", "nudge", "procurement")
    assert mine["request"]["to"] == "Apex Sterile"


@db
def test_placed_then_received_ends_the_alerts_workflow(engine):
    from services.pipeline.graph.pipeline import AgentPipeline

    _, order_id = approved_order(engine)
    assert record(engine, order_id, "placed").status_code == 200
    assert tick(engine, NOV16) == [{"transfer": order_id, "decision": "wait", "kind": "order"}]

    assert record(engine, order_id, "delivered", day="2026-11-17").status_code == 200
    assert tick(engine, NOV17) == [{"transfer": order_id, "decision": "close", "kind": "order"}]
    assert AgentPipeline(engine, engine).paused(FENTANYL) is None
    assert rows(engine, "SELECT status, delivered_at FROM purchase_order WHERE order_id = :o", o=order_id) == [
        ("delivered", NOV17)]


@db
def test_a_cancelled_order_replans_without_that_suppliers_order(engine):
    from services.pipeline.graph.pipeline import AgentPipeline

    plan_id, order_id = approved_order(engine)
    record(engine, order_id, "cancelled")

    assert tick(engine, NOV16) == [{"transfer": order_id, "decision": "replan", "kind": "order"}]

    paused = AgentPipeline(engine, engine).paused(FENTANYL)
    assert paused["waiting_for"] == "decision" and paused["plan_id"] != plan_id
    assert rows(engine, "SELECT count(*) FROM plan_action WHERE plan_id = :p AND supplier_id = 'apex-sterile'",
                p=paused["plan_id"]) == [(0,)]


@db
def test_only_procurement_or_the_supply_manager_record_orders_and_in_order(engine):
    _, order_id = approved_order(engine)

    assert record(engine, order_id, "placed", role="pharmacist").status_code == 409
    assert record(engine, order_id, "delivered").status_code == 409       # not placed yet
    assert rows(engine, "SELECT status FROM purchase_order WHERE order_id = :o", o=order_id) == [("approved",)]
    [audit] = rows(engine, "SELECT count(*) FROM audit_log WHERE action LIKE 'Order placed%'")
    assert audit == (0,)
