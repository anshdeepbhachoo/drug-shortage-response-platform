"""A simulated day end to end. Real Postgres, fake model or rules planner.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
os.environ.setdefault("API_NO_AUTOSTART", "1")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from ingestion.synthetic_loader import load_folder  # noqa: E402
from services.agents.store.approvals import approve_plan  # noqa: E402
from services.agents.pipeline.daily_run import run_day  # noqa: E402
from services.agents.data.db_data import PostgresData  # noqa: E402
from tests.test_agent import FakeModel, assessment, transfer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL")
NOV15, NOV16 = date(2026, 11, 15), date(2026, 11, 16)
DAYS = ROOT / "scenarios" / "progression" / "days"

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
    # After the migration: alembic's env.py reloads .env, which would put the model back.
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)   # never call Bedrock from tests
    monkeypatch.setattr("services.agents.agents.signal_agents.run_agent", _no_bedrock)
    engine = create_engine(URL)
    yield engine
    engine.dispose()


def rows(engine, sql, **params):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def test_day_without_a_model_still_watches_stock_and_blocks_hostile_mail(engine):
    summary = run_day(engine, engine, NOV15, inbox={"2026-11-15": ["clear.txt", "hostile.txt"]})

    assert summary["planner"] == "rules" and summary["ingested"] == 314
    assert summary["emails"] == [
        {"email": "clear.txt", "status": "waiting: reading emails needs the model"},
        {"email": "hostile.txt", "status": "needs_review"},
    ]
    alerts = summary["stock_watch"]["alerts"]
    # Critical drugs are flagged two weeks ahead (high: 10 days), all planned the same day.
    assert {a["drug"].split()[0] for a in alerts} == {"Cyclophosphamide", "Cisplatin", "Epinephrine", "Fentanyl",
                                                     "Methotrexate", "Propofol"}
    assert {a["criticality"] for a in alerts if a["drug"].startswith("Propofol")} == {"high"}
    assert rows(engine, "SELECT count(*) FROM supply_event WHERE source_type = 'stock_watch'") == [(6,)]
    assert rows(engine, "SELECT actor, action FROM audit_log WHERE action = 'Daily run'") == [("daily-run", "Daily run")]


def test_rules_planner_plans_are_checked_like_any_other(engine):
    summary = run_day(engine, engine, NOV15, inbox={}, max_plans=3)

    statuses = {a["drug"].split()[0]: a["status"] for a in summary["stock_watch"]["alerts"]}
    # Propofol is short at all six sites (scenario C): nothing can be transferred, so the
    # plan offers the escalation, which a person can choose.
    assert statuses["Propofol"] == "plan_generated"
    assert rows(engine, """SELECT count(*) FROM plan_action a JOIN plan p USING (plan_id)
                           JOIN supply_event e USING (event_id)
                           WHERE e.source_type = 'stock_watch' AND a.type = 'escalate'""")[0][0] >= 1


def test_critical_drugs_are_never_capped_others_wait(engine):
    summary = run_day(engine, engine, NOV15, inbox={}, max_plans=0)

    planned = summary["stock_watch"]["alerts"]
    assert len(planned) == 5 and all(a["criticality"] == "critical" for a in planned)
    assert summary["stock_watch"]["waiting"] == 1                 # propofol (high) waits for capacity
    second = run_day(engine, engine, NOV16, inbox={}, max_plans=3)
    assert any(a["drug"].startswith("Propofol") for a in second["stock_watch"]["alerts"])
    # Day 2 re-plans what changed but never opens a second alert for the same drug.
    assert rows(engine, "SELECT din FROM supply_event GROUP BY din HAVING count(*) > 1") == []


def test_email_with_a_model_then_stock_watch_does_not_duplicate_it(engine):
    model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])

    day1 = run_day(engine, engine, NOV15, llm=model, planner="rules", inbox={"2026-11-15": ["clear.txt"]})
    assert day1["emails"] == [{"email": "clear.txt", "status": "plan_generated"}]

    # Nov 16: the order feed now shows O-1001 late, so Central General is short of
    # cyclophosphamide by the numbers too. It already has an open alert from the email.
    day2 = run_day(engine, engine, NOV16, llm=FakeModel(), planner="rules", inbox={})
    assert all("Cyclophosphamide 500" not in a["drug"] for a in day2["stock_watch"]["alerts"])
    assert rows(engine, "SELECT count(*) FROM supply_event WHERE din = '99000001'") == [(1,)]


def test_followup_runs_in_the_evening_on_the_simulated_clock(engine):
    model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
    run_day(engine, engine, NOV15, llm=model, planner="rules", inbox={"2026-11-15": ["clear.txt"]})
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.source_type = 'supplier_email'""")
    approve_plan(engine, plan_id, "Dana Okafor", "supply_manager", PostgresData(engine, as_of=NOV15),
                 at=datetime(2026, 11, 15, 12))

    day2 = run_day(engine, engine, NOV16, llm=FakeModel(), planner="rules", inbox={})

    assert day2["followup"] == [{"transfer": f"{plan_id}-O1", "decision": "nudge"}]   # due Nov 16 12:00, 6 h late
    assert rows(engine, "SELECT decided_at::date FROM decision") == [(NOV15,)]


def test_api_runs_a_day_and_reports_it(engine):
    api = TestClient(create_app(engine, engine))

    response = api.post("/api/day/run", json={"day": "2026-11-15", "planner": "rules"})

    assert response.status_code == 200 and response.json()["day"] == "2026-11-15"
    state = api.get("/api/state", params={"as_of": "2026-11-15"}).json()
    assert state["daysRun"] == ["2026-11-15"]
    assert "clear.txt" in state["inbox"]
    assert any(a["action"] == "Daily run" and a["detail"].startswith("Day 2026-11-15") for a in state["audit"])
    assert any(e["sourceRef"].startswith("stock_watch:") for e in state["events"])


def test_api_approval_uses_the_simulated_day(engine):
    model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
    run_day(engine, engine, NOV15, llm=model, planner="rules", inbox={"2026-11-15": ["clear.txt"]})
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.source_type = 'supplier_email'""")
    api = TestClient(create_app(engine, engine))

    api.post(f"/api/plans/{plan_id}-O1/approve", json={"by": "Dana Okafor", "role": "supply", "as_of": "2026-11-15"})

    assert rows(engine, "SELECT extract(hour FROM decided_at AT TIME ZONE 'UTC')::int, decided_at::date FROM decision") == [(12, NOV15)]


# ---------------------------------------------------------
# The supply manager plans a drug from the Stock page
# ---------------------------------------------------------

def test_supply_manager_plans_a_drug_without_waiting(engine):
    load_folder(engine, DAYS / "2026-11-15")                       # data in, no automatic planning
    api = TestClient(create_app(engine, engine))

    response = api.post("/api/drugs/99000011/plan",
                        json={"by": "Dana Okafor", "role": "supply", "as_of": "2026-11-15", "site_id": "A", "planner": "rules"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["planner"] == "rules" and body["event_id"] and body["plan_id"]
    assert rows(engine, "SELECT source_type, site_id, status FROM supply_event") == [("manual", "A", "open")]
    assert rows(engine, "SELECT actor, actor_type FROM audit_log WHERE action = 'Requested a plan'") == [("Dana Okafor", "human")]
    card = next(p for p in api.get("/api/state", params={"as_of": "2026-11-15"}).json()["plans"] if p["solverRun"] == body["plan_id"])
    assert card["drugId"] == "99000011"


def test_planning_a_drug_twice_opens_the_existing_alert(engine):
    load_folder(engine, DAYS / "2026-11-15")
    api = TestClient(create_app(engine, engine))
    ask = lambda: api.post("/api/drugs/99000011/plan", json={"by": "Dana Okafor", "role": "supply", "as_of": "2026-11-15", "planner": "rules"}).json()

    first, second = ask(), ask()

    assert second == {"status": "already_open", "event_id": first["event_id"]}
    assert rows(engine, "SELECT count(*) FROM supply_event") == [(1,)]


def test_only_the_supply_manager_can_raise_a_plan(engine):
    load_folder(engine, DAYS / "2026-11-15")
    api = TestClient(create_app(engine, engine))

    for role in ("pharmacist", "procurement", "auditor"):
        response = api.post("/api/drugs/99000011/plan", json={"by": "x", "role": role, "as_of": "2026-11-15"})
        assert response.status_code == 403
    assert api.post("/api/drugs/12345678/plan", json={"by": "x", "role": "supply", "as_of": "2026-11-15"}).status_code == 404
    assert rows(engine, "SELECT count(*) FROM supply_event") == [(0,)]


def test_a_drug_not_yet_in_danger_can_still_be_planned_ahead(engine):
    load_folder(engine, DAYS / "2026-11-15")

    from services.agents.pipeline.daily_run import plan_for_drug
    result = plan_for_drug(engine, engine, "99000019", NOV15, "Dana Okafor", planner="rules")   # saline: plenty

    assert result["status"] == "no_shortage_detected"
    assert rows(engine, "SELECT status FROM supply_event") == [("monitoring",)]


# ---------------------------------------------------------
# One living alert per drug: emails and new stock refine its plan
# ---------------------------------------------------------

ESCALATE_ONLY = {"options": [{"type": "escalate", "description": "Tell the manager.", "moves": [],
                              "reasoning": "Two-week delay on top of low stock."}], "no_perfect_plan": True}


def plans_for(engine, din):
    return rows(engine, """SELECT p.status FROM plan p JOIN supply_event e USING (event_id)
                           WHERE e.din = :din ORDER BY p.created_at""", din=din)


def test_email_joins_the_drugs_alert_and_replaces_its_plan(engine):
    run_day(engine, engine, NOV15, inbox={})                                     # watch raises epinephrine
    assert plans_for(engine, "99000011") == [("pending_approval",)] or plans_for(engine, "99000011") == [("proposed",)]
    model = FakeModel(assessments=[assessment(drug_din="99000011", drug_name="Epinephrine 1 mg/mL ampule", delay_weeks=2)],
                      plans=[ESCALATE_ONLY])

    day2 = run_day(engine, engine, NOV16, llm=model, planner="rules", inbox={"2026-11-16": ["delay_epinephrine.txt"]})

    assert day2["emails"][0]["status"] == "plan_generated (updated the drug's existing alert)"
    assert rows(engine, "SELECT count(*) FROM supply_event WHERE din = '99000011'") == [(1,)]
    assert [status for (status,) in plans_for(engine, "99000011")] == ["superseded", "pending_approval"]
    [(evidence,)] = rows(engine, "SELECT detail->'evidence' FROM supply_event WHERE din = '99000011'")
    assert [e["source"] for e in evidence] == ["stock_watch", "supplier_email"]


def test_plan_is_redone_when_tomorrows_stock_makes_it_unsafe(engine, tmp_path):
    import shutil
    days = tmp_path / "days"
    shutil.copytree(ROOT / "scenarios" / "progression" / "days", days)
    usage = days / "2026-11-16" / "usage.csv"                                    # Northside's use jumps on day 2
    usage.write_text(usage.read_text().replace("B,99000001,8\n", "B,99000001,20\n"))
    model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
    run_day(engine, engine, NOV15, llm=model, planner="rules", inbox={"2026-11-15": ["clear.txt"]}, days_root=days)
    assert plans_for(engine, "99000001") == [("pending_approval",)]

    day2 = run_day(engine, engine, NOV16, llm=FakeModel(), planner="rules", inbox={}, days_root=days)

    redo = [a for a in day2["stock_watch"]["alerts"] if a["drug"].startswith("Cyclophosphamide 500")]
    assert redo and redo[0]["why"] == "plan no longer safe with today's stock"
    assert [status for (status,) in plans_for(engine, "99000001")][0] == "superseded"
    assert rows(engine, "SELECT count(*) FROM supply_event WHERE din = '99000001'") == [(1,)]


def test_api_gives_plans_their_hospitals_and_problems_their_evidence(engine):
    run_day(engine, engine, NOV15, inbox={})
    state = TestClient(create_app(engine, engine)).get("/api/state", params={"as_of": "2026-11-15"}).json()

    epinephrine = [p for p in state["plans"] if p["drugId"] == "99000011"]
    assert epinephrine and all("A" in p["sites"] for p in epinephrine)
    problem = next(e for e in state["events"] if e["drugId"] == "99000011")
    assert problem["evidence"][0]["source"] == "stock_watch" and problem["planState"]
    assert set(problem["sites"]) >= {"A"}


def test_approved_plan_is_left_to_followup_not_replaced(engine, tmp_path):
    import shutil
    days = tmp_path / "days"
    shutil.copytree(ROOT / "scenarios" / "progression" / "days", days)
    usage = days / "2026-11-16" / "usage.csv"                                    # same jump as above
    usage.write_text(usage.read_text().replace("B,99000001,8\n", "B,99000001,20\n"))
    model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
    run_day(engine, engine, NOV15, llm=model, planner="rules", inbox={"2026-11-15": ["clear.txt"]}, days_root=days)
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.din = '99000001'""")
    approve_plan(engine, plan_id, "Dana Okafor", "supply_manager", PostgresData(engine, as_of=NOV15),
                 at=datetime(2026, 11, 15, 12))

    day2 = run_day(engine, engine, NOV16, llm=FakeModel(), planner="rules", inbox={}, days_root=days)

    assert not [a for a in day2["stock_watch"]["alerts"] if a["drug"].startswith("Cyclophosphamide 500")]
    assert plans_for(engine, "99000001") == [("approved",)]


def test_stock_watch_restores_cover_without_inventing_a_supplier_delay(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})

    def moves(din):
        return rows(engine, """SELECT a.to_site, sum(a.units)::int FROM plan_action a JOIN plan p USING (plan_id)
                               JOIN supply_event e USING (event_id)
                               WHERE e.din = :din AND a.option_index = 1 GROUP BY 1 ORDER BY 1""", din=din)

    # Epinephrine: Central General (20 on hand, 10/day) must last to its Dec 5 delivery = 20 days;
    # Rural (50 on hand, 4/day, no delivery) gets 14 days. Nothing on top of the delivery date.
    assert moves("99000011") == [("A", 180), ("F", 6)]
    # Fentanyl: Regional Medical Centre has 22.5 days and a delivery on Nov 30, so it is not at risk.
    assert moves("99000013") == [("A", 4)]


def test_api_rejection_uses_the_simulated_day(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={})
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.din = '99000013'""")
    api = TestClient(create_app(engine, engine))

    api.post(f"/api/plans/{plan_id}-O1/reject",
             json={"by": "Dana Okafor", "role": "supply", "note": "not now", "as_of": "2026-11-16"})

    assert rows(engine, "SELECT decided_at::date FROM decision") == [(NOV16,)]
    assert rows(engine, "SELECT at::date FROM audit_log WHERE action = 'Rejected'") == [(NOV16,)]


def test_home_page_gets_day_reports_and_followups_in_plain_words(engine):
    model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
    run_day(engine, engine, NOV15, llm=model, planner="rules", inbox={"2026-11-15": ["clear.txt"]})
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.source_type = 'supplier_email'""")
    approve_plan(engine, plan_id, "Dana Okafor", "supply_manager", PostgresData(engine, as_of=NOV15),
                 at=datetime(2026, 11, 15, 12))
    run_day(engine, engine, NOV16, llm=FakeModel(), planner="rules", inbox={})

    state = TestClient(create_app(engine, engine)).get("/api/state", params={"as_of": "2026-11-16"}).json()

    assert set(state["dayReports"]) == {"2026-11-15", "2026-11-16"}
    assert state["dayReports"]["2026-11-16"]["followup"] == [{"transfer": f"{plan_id}-O1", "decision": "nudge"}]
    [follow] = state["followups"]
    assert (follow["from"], follow["to"], follow["units"], follow["decision"]) == ("Northside", "Central General", 180, "nudge")
    assert follow["status"] == "Nudged: no confirmation yet"
    assert follow["expectedBy"].startswith("2026-11-16T12:00")      # approved Nov 15 at noon + 24 h
