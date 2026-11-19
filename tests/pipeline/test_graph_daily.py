"""The LangGraph daily pipeline does exactly what the classic one does.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.pipeline.test_daily_run import NOV15, NOV16, ROOT, URL, engine, rows  # noqa: F401  (engine is a fixture)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from services.store.approvals import approve_plan  # noqa: E402
from services.pipeline.daily_run import DayContext, run_day  # noqa: E402
from services.data.db_data import PostgresData  # noqa: E402
from services.pipeline.graph.daily import build_daily_graph  # noqa: E402


def reset(engine):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def snapshot(engine) -> dict:
    """Everything the pipeline saved, without ids or timestamps."""

    return {
        "events": rows(engine, """SELECT din, status, kind, source_type, site_id FROM supply_event ORDER BY din, source_type"""),
        "plans": rows(engine, """SELECT e.din, p.status, p.no_perfect_plan FROM plan p JOIN supply_event e USING (event_id)
                                 ORDER BY e.din, p.status"""),
        "actions": rows(engine, """SELECT e.din, a.option_index, a.type, a.from_site, a.to_site, a.units, a.verified
                                   FROM plan_action a JOIN plan p USING (plan_id) JOIN supply_event e USING (event_id)
                                   ORDER BY e.din, a.option_index, a.from_site, a.to_site"""),
        "audit": rows(engine, "SELECT actor, actor_type, action FROM audit_log ORDER BY actor, action"),
    }


def without_ids(day: dict) -> dict:
    """Plan ids are random per run; compare follow-up by move number and decision only."""

    return {**day, "followup": [(f["transfer"].split("-")[-1], f["decision"]) for f in day["followup"]]}


def two_days(engine, orchestrator: str) -> tuple[list[dict], dict]:
    """Nov 15, approve epinephrine's transfer, Nov 16: the same story for either orchestrator."""

    day1 = run_day(engine, engine, NOV15, planner="rules", inbox={}, orchestrator=orchestrator)
    [(plan_id,)] = rows(engine, """SELECT p.plan_id FROM plan p JOIN supply_event e USING (event_id)
                                   WHERE e.din = '99000011' AND p.status = 'pending_approval'""")
    approve_plan(engine, plan_id, "Dana Okafor", "supply_manager", PostgresData(engine, as_of=NOV15), option=1,
                 at=datetime(2026, 11, 15, 12))
    day2 = run_day(engine, engine, NOV16, planner="rules", inbox={}, orchestrator=orchestrator)
    return [without_ids(day1), without_ids(day2)], snapshot(engine)


def test_graph_and_classic_pipelines_produce_the_same_days(engine):
    classic_days, classic_saved = two_days(engine, "classic")
    reset(engine)
    graph_days, graph_saved = two_days(engine, "graph")

    assert graph_days == classic_days
    assert graph_saved == classic_saved
    assert classic_days[1]["followup"]                       # the approved transfer was followed up


def test_graph_skips_planning_when_nothing_needs_a_plan(engine):
    run_day(engine, engine, NOV15, planner="rules", inbox={}, orchestrator="graph")

    # Running the same day again: every alert's plan still holds, so the graph routes past planning.
    ctx = DayContext(engine, engine, NOV15, planner="rules", inbox={})
    visited = [node for update in build_daily_graph(ctx).stream({}, stream_mode="updates") for node in update]

    assert visited == ["ingest", "inbox", "detect", "reports", "followup", "record"]


def test_the_graph_is_the_default_orchestrator(engine, monkeypatch):
    monkeypatch.delenv("PIPELINE_ORCHESTRATOR", raising=False)
    called = []
    monkeypatch.setattr("services.pipeline.graph.daily.run_daily_graph", lambda ctx: called.append(ctx.day) or {"day": "x"})

    run_day(engine, engine, NOV15, planner="rules", inbox={})

    assert called == [NOV15]
