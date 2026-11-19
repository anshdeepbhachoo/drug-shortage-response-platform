"""Transfer and order: a transfer bridges the hospitals until an order arrives, and the order restocks them.

Worked out and checked in code (orders.combined_option, orders.check_combined), approved by the supply
manager (it both moves stock and buys it), then followed up by both agents: the follow-up agent checks the
transfer and the order agent checks the order, in the same evening.

The unit tests need nothing. The pipeline tests are skipped unless TEST_DATABASE_URL points at a disposable
Postgres (owner role); in the 10-day scenario, drug 99000011 gets a combined option on Nov 15.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from services.agents.approvals import option_kind
from services.agents.checks import check_options
from services.agents.orders import ORDER_LEAD_DAYS, check_combined, combined_option, with_order_option
from services.agents.results_store import _actions
from services.agents.schemas import Move, OrderLine, PlanOption, PlanSet

TODAY = date(2026, 11, 15)
SUPPLIERS = [{"supplier_id": "apex-sterile", "supplier": "Apex Sterile", "site_id": "A"}]
SHORTFALL = {"A": 50}


def stock_rows(a_on_hand=20):
    """A uses 5 a day; with 20 units it lasts 4 days, before a 5-day order. B has plenty to spare."""

    return [{"site_id": "A", "site": "Central General", "on_hand": a_on_hand, "daily_use": 5},
            {"site_id": "B", "site": "Northside", "on_hand": 400, "daily_use": 5}]


def stock(rows):
    return {r["site_id"]: {"on_hand": r["on_hand"], "daily_use": r["daily_use"]} for r in rows}


def option(rows=None, **changes):
    values = dict(stock_rows=rows or stock_rows(), shortfall=SHORTFALL, suppliers=SUPPLIERS, today=TODAY,
                  delay_days=0, restore=True, reserve_days={})
    return combined_option(**(values | changes))


def check(opt, rows=None):
    return check_combined(opt, stock(rows or stock_rows()), SHORTFALL, SUPPLIERS, TODAY, ORDER_LEAD_DAYS, reserve_days={})


# ---------------------------------------------------------
# Working it out
# ---------------------------------------------------------

def test_a_transfer_bridges_the_hospital_until_its_order_arrives():
    combined = option()
    assert combined.type == "transfer_order"
    # Lasting 5 days to arrival + 2 days' margin at 5 a day is 35 units; A has 20: bridge 15.
    assert [(m.from_site, m.to_site, m.units) for m in combined.moves] == [("B", "A", 15)]
    [line] = combined.order_lines
    assert (line.site_id, line.units, line.expected_date) == ("A", 50, (TODAY + timedelta(days=ORDER_LEAD_DAYS)).isoformat())
    assert combined.reasoning.startswith("Order rules:")
    assert "Transfer 15 from Northside to Central General now, and order 50 for Central General from Apex Sterile" == combined.description
    assert check(combined) == []


def test_no_combined_option_when_the_order_alone_arrives_in_time():
    assert option(stock_rows(a_on_hand=100)) is None          # 20 days of stock: no bridge needed


def test_no_combined_option_when_the_bridge_moves_as_much_as_the_whole_gap():
    # A supplier delay pushes the order out 20 more days: bridging 27 days is 115 units, more than the 50 short.
    assert option(delay_days=20, restore=False) is None


def test_no_combined_option_when_no_hospital_can_spare_the_bridge():
    rows = stock_rows()
    rows[1]["on_hand"] = 90                                     # B keeps 17 days (85 units): spares 5 of 15
    assert option(rows) is None


def test_combined_goes_after_the_transfers_and_before_the_order_alone():
    transfer = PlanOption(type="transfer", description="t", moves=[Move(from_site="B", to_site="A", units=50)], reasoning="r")
    escalate = PlanOption(type="escalate", description="e", reasoning="r")
    order = PlanOption(type="order", description="o", order_lines=option().order_lines, reasoning="r")
    plan = with_order_option(PlanSet(options=[transfer, escalate], no_perfect_plan=False), order, option())
    assert [o.type for o in plan.options] == ["transfer", "transfer_order", "order", "escalate"]


# ---------------------------------------------------------
# Checking it (code)
# ---------------------------------------------------------

def changed(moves=None, lines=None):
    base = option()
    return base.model_copy(update={k: v for k, v in {"moves": moves, "order_lines": lines}.items() if v is not None})


@pytest.mark.parametrize("opt, problem", [
    (changed(moves=[Move(from_site="B", to_site="A", units=5)]), "A receives 5 units but needs 15"),
    (changed(moves=[Move(from_site="B", to_site="A", units=390)]), "minimum is 14"),
    (changed(lines=[OrderLine(site_id="A", supplier_id="apex-sterile", supplier="Apex Sterile", units=20,
                              expected_date="2026-11-20")]), "orders 20 units but needs 50"),
    (changed(lines=[OrderLine(site_id="A", supplier_id="nobody", supplier="Nobody", units=50,
                              expected_date="2026-11-20")]), "never supplied this drug"),
    (changed(moves=[]), "needs both a transfer and an order"),
])
def test_combined_checks_catch_each_problem(opt, problem):
    assert any(problem in p for p in check(opt)), check(opt)


def test_a_combined_option_is_pointless_when_the_order_alone_is_enough():
    assert any("order alone is enough" in p for p in check(option(), stock_rows(a_on_hand=100)))


def test_check_options_runs_the_combined_checks():
    context = {"suppliers": SUPPLIERS, "today": TODAY, "min_arrival_days": ORDER_LEAD_DAYS}
    passed, failed = check_options([option(), changed(moves=[Move(from_site="B", to_site="A", units=5)])],
                                   stock(stock_rows()), SHORTFALL, reserve_days={}, order_context=context)
    assert [o.type for o in passed] == ["transfer_order"] and list(failed) == [1]


def test_a_combined_option_is_saved_as_transfer_rows_and_order_rows_of_one_option():
    combined = option().model_dump()
    rows = _actions("P-1", {"proposed_options": [combined], "verified_options": [combined]})
    assert [(r["option_index"], r["type"], r["from_site"], r["to_site"], r["units"]) for r in rows] == [
        (1, "transfer", "B", "A", 15), (1, "order", None, "A", 50)]
    assert option_kind(rows) == "transfer_order"
    assert option_kind(rows[:1]) == "transfer"


# ---------------------------------------------------------
# In the pipeline (real Postgres, the 10-day scenario)
# ---------------------------------------------------------

from tests.test_daily_run import NOV15, URL, engine, rows as db_rows  # noqa: E402,F401  (engine is a fixture)

db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
DRUG = "99000011"


def client(engine):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    return TestClient(create_app(engine, engine))


def combined_plan(engine):
    from services.agents.daily_run import run_day

    run_day(engine, engine, NOV15, planner="rules", inbox={})
    [(plan_id, index)] = db_rows(engine, """
        SELECT DISTINCT a.plan_id, a.option_index FROM plan_action a JOIN plan p USING (plan_id)
        JOIN supply_event e USING (event_id)
        WHERE e.din = :din AND p.status = 'pending_approval' AND a.type = 'order'
          AND EXISTS (SELECT 1 FROM plan_action t WHERE t.plan_id = a.plan_id AND t.option_index = a.option_index
                      AND t.type = 'transfer')""", din=DRUG)
    return plan_id, index


def approve(engine, plan_id, index, role):
    return client(engine).post(f"/api/plans/{plan_id}-O{index}/approve",
                               json={"by": "Dana Okafor", "role": role, "as_of": "2026-11-15"})


@db
def test_the_plan_card_shows_the_combined_option_for_the_supply_manager_only(engine):
    plan_id, index = combined_plan(engine)

    state = client(engine).get("/api/state", params={"as_of": "2026-11-15"}).json()
    [card] = [p for p in state["plans"] if p["id"] == f"{plan_id}-O{index}"]
    assert card["optionType"] == "transfer_order" and card["chooseRoles"] == ["supply"]
    assert card["transfers"] and card["orders"]
    assert "Transfer tomorrow" in card["leadTime"] and "order expected" in card["leadTime"]

    assert approve(engine, plan_id, index, "procurement").status_code == 409


@db
def test_approving_it_records_the_orders_and_both_agents_follow_it_up(engine, monkeypatch):
    from services.agents.followup_db import approved_transfers
    from services.agents.graph.pipeline import AgentPipeline

    monkeypatch.setenv("ORDER_REPLY_STORY", "none")
    monkeypatch.setenv("ORDER_SUBMISSION", "off")
    plan_id, index = combined_plan(engine)

    result = approve(engine, plan_id, index, "supply_manager").json()
    assert result["approved"] and result["type"] == "transfer_order"
    orders = db_rows(engine, "SELECT order_id, status FROM purchase_order WHERE plan_id = :p ORDER BY order_id", p=plan_id)
    assert len(orders) == len(result["purchase_orders"]) >= 1 and {s for _, s in orders} == {"approved"}
    assert [t.plan_id for t in approved_transfers(engine)] and {t.plan_id for t in approved_transfers(engine)} == {plan_id}

    pipeline = AgentPipeline(engine, engine, use_rules=True)
    assert pipeline.paused(DRUG)["waiting_for"] == "order_follow_up"
    checks = pipeline.tick(DRUG, date(2026, 11, 16), datetime(2026, 11, 16, 18))
    kinds = {c.get("kind", "transfer") for c in checks}
    assert kinds == {"transfer", "order"}


def test_the_order_agent_counts_the_plans_bridge_transfer_as_arriving():
    from services.agents.order_agent import order_facts
    from tests.test_order_agent import SNAPSHOT, Data, an_order

    now = datetime(2026, 11, 16, 18)
    without = order_facts(an_order(), Data().stock("x"), SNAPSHOT, now, 0, None)
    bridged = order_facts(an_order(), Data().stock("x"), SNAPSHOT, now, 0, None, bridge_units=20)
    assert bridged["site_cover_days"] == without["site_cover_days"] + 4      # 20 units at 5 a day
    assert bridged["bridge_units"] == 20
