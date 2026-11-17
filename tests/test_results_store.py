"""Database tests for the platform tables: results store and audit log.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
Every test rebuilds the schema with Alembic, so never point this at a real DB.

  TEST_DATABASE_URL=postgresql+psycopg://platform_owner:pw@127.0.0.1:55432/shortage_platform
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

from services.agents.agent import process_email  # noqa: E402
from services.agents.data import CsvData  # noqa: E402
from services.agents.results_store import save_pipeline_result  # noqa: E402
from tests.test_agent import FakeModel, assessment, transfer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def engine(monkeypatch):
    """A freshly migrated schema for every test."""

    monkeypatch.setenv("DATABASE_URL_OWNER", URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(URL)
    yield engine
    engine.dispose()


def count(engine, table, where="TRUE"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one()


def pipeline_result(email="clear.txt", plans=None):
    model = FakeModel(assessments=[assessment()], plans=plans or [transfer("B", 180)])
    email_text = (MOCK / "emails" / email).read_text()
    return process_email(email_text, data=CsvData(MOCK), llm=model, today=date(2026, 11, 15))


# ---------------------------------------------------------
# Results store
# ---------------------------------------------------------

def test_plan_result_is_saved_as_event_plan_actions_and_audit(engine):
    result = pipeline_result(plans=[transfer("C", 180), transfer("B", 180)])

    saved = save_pipeline_result(engine, result, source_ref="mock/emails/clear.txt")

    with engine.connect() as conn:
        event = conn.execute(text("SELECT din, site_id, delay_days, status FROM supply_event")).one()
        plan = conn.execute(text("SELECT status, event_id FROM plan")).one()
        action = conn.execute(text("SELECT from_site, to_site, units, verified FROM plan_action")).one()
        audit = conn.execute(text("SELECT actor_type, action, refs FROM audit_log")).one()
    assert tuple(event) == ("99000001", "A", 21, "open")
    assert tuple(plan) == ("pending_approval", saved["event_id"])
    assert tuple(action) == ("B", "A", 180, True)
    assert audit.actor_type == "agent"
    assert audit.action == "Signal pipeline: plan_generated"
    assert saved["plan_id"] in audit.refs


def test_unverified_plan_is_stored_as_proposed_with_problems(engine):
    result = pipeline_result(plans=[transfer("C", 180), transfer("C", 180)])

    save_pipeline_result(engine, result, source_ref="mock/emails/clear.txt")

    assert count(engine, "plan", "status = 'proposed'") == 1
    assert count(engine, "plan_action", "NOT verified AND problems IS NOT NULL") == 1


def test_rejected_email_only_writes_audit(engine):
    result = process_email((MOCK / "emails" / "hostile.txt").read_text(), data=CsvData(MOCK), llm=FakeModel())

    saved = save_pipeline_result(engine, result, source_ref="mock/emails/hostile.txt")

    assert saved["event_id"] is None and saved["plan_id"] is None
    assert count(engine, "supply_event") == 0
    assert count(engine, "audit_log", "actor_type = 'rule'") == 1


def test_audit_log_cannot_be_changed_or_truncated(engine):
    save_pipeline_result(engine, {"status": "needs_review", "reason": "x"}, source_ref="t")
    for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(sql))
    assert count(engine, "audit_log") == 1


@pytest.mark.parametrize("sql", [
    "INSERT INTO supply_event (event_id, din, kind, source_type, source_ref, confidence) VALUES ('E', '123', 'delay', 'supplier_email', 't', 0.9)",
    "INSERT INTO supply_event (event_id, din, kind, source_type, source_ref, confidence) VALUES ('E', '99000001', 'delay', 'supplier_email', 't', 1.5)",
    "INSERT INTO plan (plan_id, event_id, source) VALUES ('P', 'missing', 'rules')",
])
def test_bad_platform_rows_are_rejected(engine, sql):
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text(sql))
