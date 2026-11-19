"""The LangGraph agentic pipeline: pauses for people, resumes from the API, loops on follow-up and replans.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.pipeline.test_daily_run import NOV15, NOV16, URL, engine, plans_for, rows  # noqa: F401  (engine is a fixture)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from services.pipeline.daily_run import run_day  # noqa: E402
from services.agents.followup_schemas import FollowUpAction  # noqa: E402
from services.pipeline.graph.pipeline import AgentPipeline  # noqa: E402
from tests.agents.test_agent import FakeModel, assessment  # noqa: E402

EPI = "99000011"


def pending_plan(engine, din=EPI):
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.din = :din AND p.status = 'pending_approval'""", din=din)
    return plan_id


def approve(engine, plan_id, day="2026-11-15"):
    return TestClient(create_app(engine, engine)).post(f"/api/plans/{plan_id}-O1/approve",
                                                       json={"by": "Dana Okafor", "role": "supply", "as_of": day}).json()


def test_a_planned_alert_pauses_for_a_person_and_its_state_is_saved_in_postgres(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})

    paused = AgentPipeline(engine, engine, use_rules=True).paused(EPI)   # a new instance: state came from Postgres

    assert paused == {"waiting_for": "decision", "alert_id": paused["alert_id"], "plan_id": pending_plan(engine)}
    assert rows(engine, "SELECT count(*) > 0 FROM checkpoints WHERE thread_id = :t", t=f"alert:{EPI}") == [(True,)]


def test_approving_in_the_api_resumes_the_alert_into_follow_up(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})
    plan_id = pending_plan(engine)

    result = approve(engine, plan_id)

    assert result["approved"] is True
    paused = AgentPipeline(engine, engine).paused(EPI)
    assert (paused["waiting_for"], paused["plan_id"]) == ("follow_up", plan_id)


def test_the_evening_follow_up_runs_inside_the_pipeline_and_waits_for_the_next_evening(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})
    plan_id = pending_plan(engine)
    approve(engine, plan_id)

    day2 = run_day(engine, engine, NOV16, planner="rules", inbox={})

    moves = rows(engine, "SELECT action_id FROM plan_action WHERE plan_id = :p AND option_index = 1", p=plan_id)
    assert {f["transfer"] for f in day2["followup"]} == {m for (m,) in moves}
    assert AgentPipeline(engine, engine).waiting_for(EPI) == "follow_up"          # not all closed: checks again tomorrow
    assert rows(engine, """SELECT count(*) FROM audit_log WHERE actor = 'follow-up-agent'
                           AND object LIKE :p""", p=f"{plan_id}%") == [(len(moves),)]


def test_rejecting_in_the_api_ends_the_alerts_workflow(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})
    plan_id = pending_plan(engine)

    TestClient(create_app(engine, engine)).post(f"/api/plans/{plan_id}-O1/reject",
                                                json={"by": "Dana Okafor", "role": "supply", "note": "Not now"})

    assert AgentPipeline(engine, engine).paused(EPI) is None
    assert plans_for(engine, EPI) == [("rejected",)]


def test_a_refused_approval_loops_back_to_planning_on_todays_stock(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})
    plan_id = pending_plan(engine)
    with engine.begin() as conn:  # the donors used most of their stock overnight
        from sqlalchemy import text
        conn.execute(text("UPDATE inventory_snapshot SET on_hand = 5 WHERE din = :d AND site_id IN ('B', 'C')"), {"d": EPI})

    result = approve(engine, plan_id)

    assert result["approved"] is False
    paused = AgentPipeline(engine, engine).paused(EPI)
    assert paused["waiting_for"] == "decision" and paused["plan_id"] != plan_id          # a fresh plan to decide on
    assert plans_for(engine, EPI) == [("superseded",), ("pending_approval",)]   # refused (broken), then replaced
    assert rows(engine, "SELECT count(*) FROM audit_log WHERE action = 'Approval refused: plan no longer safe'") == [(1,)]


def test_a_follow_up_replan_loops_back_and_never_reuses_the_failed_donor(engine, monkeypatch):
    run_day(engine, engine, NOV15, planner="rules", inbox={})
    plan_id = pending_plan(engine)
    approve(engine, plan_id)
    [(action_id, donor)] = rows(engine, """SELECT action_id, from_site FROM plan_action WHERE plan_id = :p
                                           AND option_index = 1 ORDER BY units DESC LIMIT 1""", p=plan_id)

    def failed_transfer(*args, **kwargs):  # the follow-up agent decides the biggest transfer is not happening
        return [FollowUpAction(at=datetime(2026, 11, 16, 18), request_id=action_id, decision="replan", decided_by="rule",
                               reason="Donor cannot ship.", facts={}, needs_human=True)]
    monkeypatch.setattr("services.pipeline.graph.pipeline.run_followup", failed_transfer)

    AgentPipeline(engine, engine, use_rules=True).tick(EPI, NOV16, datetime(2026, 11, 16, 18))

    paused = AgentPipeline(engine, engine).paused(EPI)
    assert paused["waiting_for"] == "decision" and paused["plan_id"] != plan_id
    sources = rows(engine, "SELECT DISTINCT from_site FROM plan_action WHERE plan_id = :p AND type = 'transfer'", p=paused["plan_id"])
    assert (donor,) not in sources


def test_intake_holds_a_hostile_notice_and_hands_a_clean_one_to_the_alert(engine):
    from pathlib import Path

    emails = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "mock" / "emails"
    model = FakeModel(assessments=[assessment()])
    pipeline = AgentPipeline(engine, engine, llm=model)

    held = pipeline.read_notice((emails / "hostile.txt").read_text(), NOV15)
    clean = pipeline.read_notice((emails / "clear.txt").read_text(), NOV15)

    assert held["outcome"] == "held" and held["result"]["status"] == "needs_review"
    assert clean["outcome"] == "plan" and clean["event"]["drug_din"] == "99000001"
