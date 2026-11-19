"""Agents on the database: shortage signals, reference tools, data-source switch, workflow step.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
Every test rebuilds the schema with Alembic, so never point this at a real DB.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from ingestion.synthetic_loader import load_folder  # noqa: E402
from ingestion.workflow import nightly_tasks, run_workflow  # noqa: E402
from services.data.data_source import default_data  # noqa: E402
from services.data.db_data import PostgresData  # noqa: E402
from services.pipeline.shortage_signals import pending_reports, run_shortage_signals  # noqa: E402
from services.agents.tools import build_tool_registries  # noqa: E402
from tests.test_agent import FakeModel, transfer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
URL = os.environ.get("TEST_DATABASE_URL")
TODAY = date(2026, 11, 15)

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def engine(monkeypatch):
    """A freshly migrated schema with the 3-site baseline loaded."""

    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    load_folder(engine, BASELINE)
    yield engine
    engine.dispose()


def count(engine, table, where="TRUE"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one()


def add_shortages(engine, *reports):
    """reports: (report_id, din, status, tier3, estimated_end 'YYYY-MM-DD' or None)."""

    with engine.begin() as conn:
        run_id = conn.execute(text("INSERT INTO ingestion_run (source, state) VALUES ('test', 'running') RETURNING run_id")).scalar_one()
        snapshot = conn.execute(text("""INSERT INTO raw_snapshot (run_id, source, endpoint, request_params, payload, payload_sha256)
                                        VALUES (:r, 'test', '/search', '{}', '{}', repeat('0', 64)) RETURNING snapshot_id"""),
                                {"r": run_id}).scalar_one()
        for report_id, din, status, tier3, end in reports:
            conn.execute(text("""INSERT INTO shortage_report (report_id, din, status, tier3, shortage_reason,
                                                              estimated_end_date, updated_date, snapshot_id)
                                 VALUES (:id, :din, :status, :tier3, 'Manufacturing delay', :end, now(), :snap)"""),
                         {"id": report_id, "din": din, "status": status, "tier3": tier3,
                          "end": datetime.fromisoformat(end).replace(tzinfo=timezone.utc) if end else None, "snap": snapshot})
    return snapshot


# ---------------------------------------------------------
# Shortage reports -> planned supply events
# ---------------------------------------------------------

def test_shortage_report_becomes_a_planned_event(engine):
    add_shortages(engine, (501, "99000001", "active_confirmed", True, "2026-12-06"))
    model = FakeModel(plans=[transfer("B", 180)])

    summary = run_shortage_signals(engine, PostgresData(engine), llm=model, today=TODAY)

    assert summary["reports"] == 1 and summary["plan_generated"] == 1
    assert model.count("assessment") == 0          # no email, no assessment agent
    with engine.connect() as conn:
        event = conn.execute(text("SELECT din, kind, source_type, source_ref, delay_days, confidence FROM supply_event")).one()
        plan = conn.execute(text("SELECT status FROM plan")).scalar_one()
        audit = conn.execute(text("SELECT actor, refs FROM audit_log")).one()
    assert tuple(event) == ("99000001", "shortage", "shortage_report", "shortage_report:501", 21, 1)
    assert plan == "pending_approval"
    assert audit.actor == "shortage-signal" and "shortage_report:501" in audit.refs


def test_each_report_is_planned_once(engine):
    add_shortages(engine, (501, "99000001", "active_confirmed", True, "2026-12-06"))
    run_shortage_signals(engine, PostgresData(engine), llm=FakeModel(plans=[transfer("B", 180)]), today=TODAY)

    model = FakeModel()
    again = run_shortage_signals(engine, PostgresData(engine), llm=model, today=TODAY)

    assert again["reports"] == 0 and model.calls == []
    assert count(engine, "supply_event") == 1


def test_only_active_reports_for_tracked_drugs_are_used_tier3_first(engine):
    add_shortages(engine,
                  (1, "99000003", "active_confirmed", False, None),
                  (2, "99000001", "active_confirmed", True, None),
                  (3, "99000004", "resolved", True, None),        # resolved
                  (4, "02242974", "active_confirmed", True, None))  # real DIN we don't track

    assert [r["report_id"] for r in pending_reports(engine)] == [2, 1]


def test_failed_planning_leaves_report_pending(engine):
    add_shortages(engine, (501, "99000001", "active_confirmed", True, "2026-12-06"))

    summary = run_shortage_signals(engine, PostgresData(engine), llm=FakeModel(plans=[RuntimeError("down")]), today=TODAY)

    assert summary["error"] == 1
    assert count(engine, "supply_event") == 0
    assert count(engine, "audit_log", "action = 'Signal pipeline: error'") == 1
    assert [r["report_id"] for r in pending_reports(engine)] == [501]


def test_covered_drug_is_saved_as_monitoring(engine):
    # 99000004 at the baseline sites easily covers a 2-day horizon.
    add_shortages(engine, (9, "99000004", "active_confirmed", False, "2026-11-17"))

    summary = run_shortage_signals(engine, PostgresData(engine), llm=FakeModel(), today=TODAY)

    assert summary["no_shortage_detected"] == 1
    assert count(engine, "supply_event", "status = 'monitoring'") == 1
    assert pending_reports(engine) == []


# ---------------------------------------------------------
# Reference tools on real tables
# ---------------------------------------------------------

def test_shortage_and_dpd_tools_read_the_database(engine):
    snapshot = add_shortages(engine, (501, "99000001", "active_confirmed", True, "2026-12-06"))
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO drug_product (drug_code, din, brand_name, company_name, snapshot_id)
                             VALUES (7, '99000001', 'CYCLO 500', 'EXAMPLE', :s)"""), {"s": snapshot})
        conn.execute(text("INSERT INTO product_status (drug_code, status_code, snapshot_id) VALUES (7, 2, :s)"), {"s": snapshot})
        conn.execute(text("""INSERT INTO active_ingredient (drug_code, ingredient_name, ingredient_base, strength, strength_unit,
                                                            strength_num, row_hash, snapshot_id)
                             VALUES (7, 'CYCLOPHOSPHAMIDE', 'CYCLOPHOSPHAMIDE', '500', 'MG', 500, repeat('1', 64), :s)"""), {"s": snapshot})
        conn.execute(text("INSERT INTO route (drug_code, route_code, route_name, snapshot_id) VALUES (7, 1, 'INTRAVENOUS', :s)"), {"s": snapshot})

    _, planning = build_tool_registries(PostgresData(engine))

    reports = planning.execute("get_shortage_reports", {"din": "99000001"})
    product = planning.execute("get_drug_product", {"din": "99000001"})
    assert reports[0]["report_id"] == 501 and reports[0]["tier3"] is True
    assert product["current_status"] == "Marketed"
    assert product["ingredients"][0]["strength_num"] == 500.0
    assert product["routes"] == ["INTRAVENOUS"]
    assert planning.execute("get_drug_product", {"din": "99000002"}) is None
    json.dumps([reports, product])                  # tool results must be JSON for Bedrock


def test_data_source_switch_picks_postgres(engine, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_SOURCE", "postgres")
    monkeypatch.setenv("DATABASE_URL_READONLY", URL)

    data = default_data()

    assert isinstance(data, PostgresData)
    assert data.drug("99000001")["name"].startswith("Cyclophosphamide")


# ---------------------------------------------------------
# Workflow step
# ---------------------------------------------------------

def test_signals_step_skips_without_a_model(engine, monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    add_shortages(engine, (501, "99000001", "active_confirmed", True, "2026-12-06"))

    results = run_workflow("nightly", nightly_tasks(), engine, only={"signals"}, log=lambda _: None)

    assert results[0].state == "succeeded"
    assert results[0].stats == {"skipped": "no model configured (BEDROCK_MODEL_ID)", "pending": 1}
    assert count(engine, "supply_event") == 0


def test_signals_step_plans_with_a_model(engine):
    add_shortages(engine, (501, "99000001", "active_confirmed", True, "2026-12-06"))
    tasks = nightly_tasks(llm=FakeModel(plans=[transfer("B", 180)]), data=PostgresData(engine))

    results = run_workflow("nightly", tasks, engine, only={"signals"}, log=lambda _: None)

    assert results[0].state == "succeeded"
    assert results[0].stats["reports"] == 1
    assert count(engine, "supply_event", "source_type = 'shortage_report'") == 1
