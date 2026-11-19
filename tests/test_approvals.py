"""Approving and rejecting plans. Real Postgres, fake model.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

from ingestion.synthetic_loader import load_folder  # noqa: E402
from services.agents.store import approvals  # noqa: E402
from services.agents.agents.signal_agents import process_email  # noqa: E402
from services.agents.store.approvals import ApprovalError, approve_plan, pending_plans, reject_plan  # noqa: E402
from services.agents.data.db_data import PostgresData  # noqa: E402
from services.agents.store.results_store import save_pipeline_result  # noqa: E402
from tests.test_agent import FakeModel, assessment  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

THREE_OPTIONS = {
    "options": [
        {"type": "transfer", "description": "Move 180 from B to A.",
         "moves": [{"from_site": "B", "to_site": "A", "units": 180}], "reasoning": "B has spare."},
        {"type": "substitute", "description": "Use 1000 mg vials.", "moves": [], "reasoning": "Approved table."},
        {"type": "escalate", "description": "Tell the manager.", "moves": [], "reasoning": "Just in case."},
    ],
    "no_perfect_plan": False,
}


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    load_folder(engine, BASELINE)
    yield engine
    engine.dispose()


@pytest.fixture
def plan_id(engine):
    """A saved plan for the clear.txt delay: option 1 transfer (verified), 2 substitute, 3 escalate."""

    model = FakeModel(assessments=[assessment()], plans=[THREE_OPTIONS])
    email = (MOCK / "emails" / "clear.txt").read_text()
    result = process_email(email, data=PostgresData(engine), llm=model, today=date(2026, 11, 15))
    assert result["status"] == "plan_generated"
    return save_pipeline_result(engine, result, source_ref="mock/emails/clear.txt")["plan_id"]


def one(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).one_or_none()


def count(engine, table, where="TRUE"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one()


def approve(engine, plan_id, role="pharmacy_manager", **kwargs):
    return approve_plan(engine, plan_id, "Priya Nair", role, PostgresData(engine), **kwargs)


# ---------------------------------------------------------
# Saved plans carry what approval needs
# ---------------------------------------------------------

def test_saved_plan_records_options_and_check_numbers(engine, plan_id):
    detail = one(engine, "SELECT detail FROM plan WHERE plan_id = :p", p=plan_id).detail
    assert detail["shortfall"] == {"A": 180} and detail["cover_days"]["B"] == 21

    [listed] = pending_plans(engine)
    assert listed["plan_id"] == plan_id and listed["status"] == "pending_approval"
    assert [(o["option"], o["type"], o["verified"]) for o in listed["options"]] == [
        (1, "transfer", True), (2, "substitute", False), (3, "escalate", True)]   # substitute names no approved DIN
    assert listed["options"][0]["moves"] == ["B->A 180"]


# ---------------------------------------------------------
# Approving
# ---------------------------------------------------------

def test_approve_picks_first_verified_option_and_records_everything(engine, plan_id):
    result = approve(engine, plan_id, note="Confirmed with Northside")

    assert result["approved"] is True and result["option"] == 1 and result["type"] == "transfer"
    decision = one(engine, "SELECT actor, actor_role, decision, option_index, note FROM decision")
    assert tuple(decision) == ("Priya Nair", "pharmacy_manager", "approved", 1, "Confirmed with Northside")
    assert one(engine, "SELECT status FROM plan").status == "approved"
    assert one(engine, "SELECT status FROM supply_event").status == "mitigated"
    audit = one(engine, "SELECT actor_type, action, refs FROM audit_log WHERE action = 'Approved'")
    assert audit.actor_type == "human" and plan_id in audit.refs and f"{plan_id}-O1" in audit.refs


@pytest.mark.parametrize("role", ["pharmacist", "procurement"])
def test_wrong_role_cannot_approve_a_transfer_and_nothing_is_written(engine, plan_id, role):
    with pytest.raises(ApprovalError, match=f"A {role} cannot approve a transfer"):
        approve(engine, plan_id, role=role)

    assert count(engine, "decision") == 0
    assert one(engine, "SELECT status FROM plan").status == "pending_approval"


@pytest.mark.parametrize("option, message", [(2, "did not pass the checks"), (9, "has no option 9")])
def test_only_checked_options_can_be_approved(engine, plan_id, option, message):
    with pytest.raises(ApprovalError, match=message):
        approve(engine, plan_id, role="pharmacist", option=option)
    assert count(engine, "decision") == 0


def test_supply_manager_can_choose_the_escalation_instead(engine, plan_id):
    result = approve(engine, plan_id, role="supply_manager", option=3, note="Calling Meridian myself")

    assert result["approved"] is True and result["type"] == "escalate"
    assert one(engine, "SELECT option_index, note FROM decision") == (3, "Calling Meridian myself")
    assert one(engine, "SELECT status FROM supply_event").status == "mitigated"


def test_a_plan_is_decided_once(engine, plan_id):
    approve(engine, plan_id)

    with pytest.raises(ApprovalError, match="is approved"):
        approve(engine, plan_id)
    with pytest.raises(ApprovalError, match="can no longer be rejected"):
        reject_plan(engine, plan_id, "Someone", "pharmacist", "Changed my mind")
    assert count(engine, "decision") == 1


def test_stale_transfer_is_refused_and_plan_marked_broken(engine, plan_id):
    # Northside's usage jumps from 8 to 20 a day after the plan was made: sending
    # 180 would leave it 220 / 20 = 11 days, under its 21-day minimum.
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO usage_record (site_id, din, usage_date, units_used, window_days, source_ref)
                             VALUES ('B', '99000001', '2026-11-16', 20, 14, 'site report')"""))

    result = approve(engine, plan_id)

    assert result["approved"] is False and result["status"] == "broken"
    assert any("B would have 11.0 days" in p for p in result["problems"])
    assert count(engine, "decision") == 0
    assert one(engine, "SELECT status FROM plan").status == "broken"
    assert one(engine, "SELECT status FROM supply_event").status == "open"     # still needs a response
    refused = one(engine, "SELECT actor_type, detail FROM audit_log WHERE action = 'Approval refused: plan no longer safe'")
    assert refused.actor_type == "rule" and refused.detail["attempted_by"] == "Priya Nair"
    with pytest.raises(ApprovalError, match="is broken"):
        approve(engine, plan_id)


def test_unverified_plan_cannot_be_approved(engine):
    unsafe = {"options": [{"type": "transfer", "description": "Too much from C.",
                           "moves": [{"from_site": "C", "to_site": "A", "units": 180}], "reasoning": "x"}],
              "no_perfect_plan": True}
    model = FakeModel(assessments=[assessment()], plans=[unsafe, unsafe])
    result = process_email((MOCK / "emails" / "clear.txt").read_text(), data=PostgresData(engine), llm=model,
                           today=date(2026, 11, 15))
    plan_id = save_pipeline_result(engine, result, source_ref="t")["plan_id"]

    with pytest.raises(ApprovalError, match="is proposed"):
        approve(engine, plan_id)


# ---------------------------------------------------------
# Rejecting
# ---------------------------------------------------------

def test_reject_needs_a_reason_and_records_it(engine, plan_id):
    with pytest.raises(ApprovalError, match="needs a reason"):
        reject_plan(engine, plan_id, "Priya Nair", "pharmacist", "  ")

    result = reject_plan(engine, plan_id, "Priya Nair", "pharmacist", "Clinical team prefers to wait")

    assert result["status"] == "rejected"
    decision = one(engine, "SELECT decision, option_index, note FROM decision")
    assert tuple(decision) == ("rejected", None, "Clinical team prefers to wait")
    assert one(engine, "SELECT status FROM plan").status == "rejected"


def test_unknown_role_cannot_reject(engine, plan_id):
    with pytest.raises(ApprovalError, match="cannot decide plans"):
        reject_plan(engine, plan_id, "Bot", "agent", "No")


# ---------------------------------------------------------
# Database rules and the CLI
# ---------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "INSERT INTO decision (plan_id, actor, actor_role, decision) VALUES (:p, 'x', 'y', 'approved')",        # no option
    "INSERT INTO decision (plan_id, actor, actor_role, decision, option_index) VALUES (:p, 'x', 'y', 'rejected', 1)",
])
def test_database_rejects_inconsistent_decisions(engine, plan_id, sql):
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text(sql), {"p": plan_id})


def test_cli_lists_and_approves(engine, plan_id, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", URL)

    assert approvals.main(["list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["plan_id"] == plan_id

    assert approvals.main(["approve", plan_id, "--by", "Priya Nair", "--role", "pharmacy_manager"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "approved"

    assert approvals.main(["approve", plan_id, "--by", "Priya Nair", "--role", "pharmacy_manager"]) == 2
    assert "Not allowed" in capsys.readouterr().err
