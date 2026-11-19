"""Order options: worked out and checked in code, approved by a person, recorded as an approved purchase order.

Fixture drug 99000001 (baseline_three_site): A has 60 units and uses 10 a day (6 days), Meridian Pharma
has supplied A. Restoring 3 weeks of cover, A needs 150 units; a new order arrives in 5 days (standard
lead time), before A runs out. With a 3-week supplier delay, the order would arrive in 26 days: too late.

The database tests are skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.agents.agents.signal_agents import _plan_for_event, check_plan, gap_for_event
from services.agents.rules.checks import check_options
from services.agents.data.data import CsvData
from services.agents.agents.orders import ORDER_LEAD_DAYS, check_order, order_option, with_order_option
from services.agents.rules.rules_planner import rules_planner
from services.agents.agents.schemas import OrderLine, PlanOption, PlanSet, SupplyEvent

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
TODAY = date(2026, 11, 15)
DIN = "99000001"
URL = os.environ.get("TEST_DATABASE_URL")


def event(delay_weeks=3):
    return SupplyEvent(drug_din=DIN, drug_name="Cyclophosphamide 500 mg vial", delay_weeks=delay_weeks,
                       confidence=1, next_action="plan", reason="test")


def gap(data, restore=True):
    result = gap_for_event(event(), {}, data, TODAY, restore=restore)
    assert result["ok"]
    return result


def stock_of(data):
    return {r["site_id"]: {"on_hand": r["on_hand"], "daily_use": r["daily_use"]} for r in data.stock(DIN)}


def an_order(**line):
    values = {"site_id": "A", "supplier_id": "meridian-pharma", "supplier": "Meridian Pharma", "units": 150,
              "expected_date": (TODAY + timedelta(days=ORDER_LEAD_DAYS)).isoformat()} | line
    return PlanOption(type="order", description="order", order_lines=[OrderLine(**values)], reasoning="test")


# ---------------------------------------------------------
# Working out the order
# ---------------------------------------------------------

def test_suppliers_come_from_order_history():
    assert CsvData(BASELINE).suppliers(DIN) == [{"supplier_id": "meridian-pharma", "supplier": "Meridian Pharma", "site_id": "A"}]
    assert CsvData(BASELINE).suppliers("99000002") == []


def test_order_option_covers_each_short_hospital_from_a_known_supplier():
    data = CsvData(BASELINE)
    g = gap(data)
    option = order_option(stock_rows=data.stock(DIN), shortfall=g["shortfall"], suppliers=data.suppliers(DIN),
                          today=TODAY, delay_days=g["delay_days"], restore=True)

    assert g["shortfall"] == {"A": 150}
    assert option.type == "order" and option.moves == []
    [line] = option.order_lines
    assert (line.site_id, line.supplier_id, line.units, line.expected_date) == ("A", "meridian-pharma", 150, "2026-11-20")
    assert option.reasoning.startswith("Order rules")   # the UI marks it as rules, not an agent
    assert option.description == "Order 150 for Central General from Meridian Pharma"


def test_no_order_when_a_supplier_delay_means_it_arrives_too_late():
    data = CsvData(BASELINE)
    g = gap(data, restore=False)   # a 3-week supplier delay applies to new orders too
    assert order_option(stock_rows=data.stock(DIN), shortfall=g["shortfall"], suppliers=data.suppliers(DIN),
                        today=TODAY, delay_days=g["delay_days"], restore=False) is None


def test_no_order_without_a_known_supplier():
    data = CsvData(BASELINE)
    g = gap(data)
    assert order_option(stock_rows=data.stock(DIN), shortfall=g["shortfall"], suppliers=[],
                        today=TODAY, delay_days=g["delay_days"], restore=True) is None


def test_order_goes_after_transfers_and_planner_orders_are_dropped():
    transfer = PlanOption(type="transfer", description="t", moves=[{"from_site": "B", "to_site": "A", "units": 1}], reasoning="r")
    escalate = PlanOption(type="escalate", description="e", reasoning="r")
    from_model = PlanOption(type="order", description="model's own order", reasoning="r")
    plan = with_order_option(PlanSet(options=[transfer, from_model, escalate], no_perfect_plan=False), an_order())
    assert [o.type for o in plan.options] == ["transfer", "order", "escalate"]
    assert plan.options[1].description == "order"


def test_both_planners_get_the_order_option_and_it_passes_the_checks():
    data = CsvData(BASELINE)
    result = _plan_for_event(event(), {}, data, None, TODAY, None, planner=rules_planner, restore=True)
    types = [o["type"] for o in result["verified_options"]]
    assert "order" in types
    # Code's options come right after the transfers: the combined transfer and order (when an order alone
    # arrives too close to running out), then the order alone.
    code = [t for t in types if t in ("transfer_order", "order")]
    assert types.index(code[0]) == sum(t == "transfer" for t in types) and code[-1] == "order"


# ---------------------------------------------------------
# Checking it
# ---------------------------------------------------------

def check(option, data=None):
    data = data or CsvData(BASELINE)
    return check_order(option, stock_of(data), {"A": 150}, data.suppliers(DIN), TODAY, min_arrival_days=ORDER_LEAD_DAYS)


def test_a_correct_order_passes():
    assert check(an_order()) == []


@pytest.mark.parametrize("line, problem", [
    ({"supplier_id": "acme", "supplier": "Acme"}, "Acme has never supplied this drug."),
    ({"units": 100}, "A orders 100 units but needs 150."),
    ({"units": 5000}, "more than 60 days of supply"),
    ({"expected_date": "2026-11-16"}, "the earliest possible is 5"),
    ({"expected_date": "2026-11-25"}, "A runs out in 6.0 days, before its order arrives in 10."),
    ({"site_id": "B"}, "B is not listed as requiring stock."),
])
def test_order_checks_catch_each_problem(line, problem):
    problems = check(an_order(**line))
    assert any(problem in p for p in problems), problems


def test_every_short_hospital_needs_an_order_line():
    data = CsvData(BASELINE)
    problems = check_order(an_order(), stock_of(data), {"A": 150, "C": 10}, data.suppliers(DIN), TODAY, ORDER_LEAD_DAYS)
    assert "C needs 10 units but has no order line." in problems


def test_an_order_cannot_be_checked_without_supplier_data():
    data = CsvData(BASELINE)
    passed, failed = check_options([an_order()], stock_of(data), {"A": 150})
    assert passed == [] and "supplier and lead-time data" in failed[0][0]


def test_check_plan_rejects_an_order_the_model_made_up():
    data = CsvData(BASELINE)
    g = gap(data)
    plan = PlanSet(options=[an_order(units=40)], no_perfect_plan=False)
    verified, rejected = check_plan(event(), g, plan, data)
    assert verified == [] and "needs 150" in rejected[0][0]


# ---------------------------------------------------------
# Saving and approving (real Postgres)
# ---------------------------------------------------------

db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def engine(monkeypatch):
    pytest.importorskip("psycopg")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from ingestion.synthetic_loader import load_folder

    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    load_folder(engine, BASELINE)
    yield engine
    engine.dispose()


def saved_order_plan(engine):
    """A stock-watch style plan (restore cover) for 99000001; returns (plan_id, order option number)."""

    from services.agents.data.db_data import PostgresData
    from services.agents.store.results_store import save_pipeline_result

    result = _plan_for_event(event(), event().model_dump(), PostgresData(engine), None, TODAY, None,
                             planner=rules_planner, restore=True)
    plan_id = save_pipeline_result(engine, result, source_ref="test:order", source_type="manual", kind="shortage")["plan_id"]
    option = 1 + [o["type"] for o in result["proposed_options"]].index("order")
    return plan_id, option


def rows(engine, sql, **params):
    from sqlalchemy import text

    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


@db
def test_order_lines_are_saved_with_supplier_and_date(engine):
    plan_id, option = saved_order_plan(engine)
    [line] = rows(engine, "SELECT * FROM plan_action WHERE plan_id = :p AND option_index = :o", p=plan_id, o=option)
    assert (line["type"], line["from_site"], line["to_site"], line["units"], line["supplier_id"], line["verified"]) == (
        "order", None, "A", 150, "meridian-pharma", True)
    assert line["expected_date"] == date(2026, 11, 20)


@db
def test_approving_an_order_records_an_approved_purchase_order(engine):
    from services.agents.store.approvals import approve_plan
    from services.agents.data.db_data import PostgresData

    plan_id, option = saved_order_plan(engine)
    result = approve_plan(engine, plan_id, "Sam Lee", "procurement", PostgresData(engine, as_of=TODAY), option=option)

    assert result["approved"] and result["type"] == "order"
    [order] = rows(engine, "SELECT * FROM purchase_order WHERE plan_id = :p", p=plan_id)
    assert (order["status"], order["site_id"], order["supplier_id"], order["qty"], order["expected_date"]) == (
        "approved", "A", "meridian-pharma", 150, date(2026, 11, 20))
    assert result["purchase_orders"] == [order["order_id"]]
    # Approved is not placed: it does not count as stock on the way.
    assert all(o["order_id"] != order["order_id"] for o in PostgresData(engine).orders(DIN))
    [audit] = rows(engine, "SELECT actor, actor_type, refs FROM audit_log WHERE action = 'Approved'")
    assert audit["actor_type"] == "human" and order["order_id"] in audit["refs"]


@db
def test_only_procurement_or_the_supply_manager_can_approve_an_order(engine):
    from services.agents.store.approvals import ApprovalError, approve_plan
    from services.agents.data.db_data import PostgresData

    plan_id, option = saved_order_plan(engine)
    with pytest.raises(ApprovalError, match="cannot approve a order"):
        approve_plan(engine, plan_id, "Priya Nair", "pharmacist", PostgresData(engine, as_of=TODAY), option=option)
    assert rows(engine, "SELECT * FROM purchase_order WHERE plan_id = :p", p=plan_id) == []


@db
def test_an_order_that_would_now_arrive_too_late_is_refused(engine):
    from sqlalchemy import text

    from services.agents.store.approvals import approve_plan
    from services.agents.data.db_data import PostgresData

    plan_id, option = saved_order_plan(engine)
    with engine.begin() as conn:   # A's stock dropped to 2 days since the plan was made
        conn.execute(text("UPDATE inventory_snapshot SET on_hand = 20 WHERE site_id = 'A' AND din = :d"), {"d": DIN})
    result = approve_plan(engine, plan_id, "Sam Lee", "procurement", PostgresData(engine, as_of=TODAY), option=option)

    assert result["approved"] is False and result["status"] == "broken"
    assert "before its order arrives" in result["problems"][0]
    assert rows(engine, "SELECT * FROM purchase_order WHERE plan_id = :p", p=plan_id) == []


@db
def test_the_lead_time_starts_when_the_order_is_approved(engine):
    from services.agents.store.approvals import approve_plan
    from services.agents.data.db_data import PostgresData

    plan_id, option = saved_order_plan(engine)   # planned Nov 15, expected Nov 20
    later = TODAY + timedelta(days=1)            # approved a day later: A still lasts until it arrives
    result = approve_plan(engine, plan_id, "Sam Lee", "procurement", PostgresData(engine, as_of=later), option=option)

    assert result["approved"]
    [order] = rows(engine, "SELECT expected_date FROM purchase_order WHERE plan_id = :p", p=plan_id)
    assert order["expected_date"] == date(2026, 11, 21)
