"""Operational tables: synthetic loader and PostgresData (the agents' DB data source).

Skipped unless TEST_DATABASE_URL points at a disposable Postgres (owner role).
Every test rebuilds the schema with Alembic, so never point this at a real DB.

  TEST_DATABASE_URL=postgresql+psycopg://platform_owner:pw@127.0.0.1:55432/shortage_platform
  TEST_DATABASE_URL_READONLY=postgresql+psycopg://readonly_app:pw@127.0.0.1:55432/shortage_platform   # optional
"""

from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import ProgrammingError  # noqa: E402

from ingestion.synthetic_loader import load_folder, load_progression, progression_folders  # noqa: E402
from services.agents.agents.signal_agents import process_email  # noqa: E402
from services.agents.data.data import CsvData  # noqa: E402
from services.agents.data.db_data import PostgresData  # noqa: E402
from tests.test_agent import FakeModel, assessment, transfer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
DAYS = ROOT / "scenarios" / "progression" / "days"
URL = os.environ.get("TEST_DATABASE_URL")
READONLY_URL = os.environ.get("TEST_DATABASE_URL_READONLY")

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


@pytest.fixture
def mock_loaded(engine):
    load_folder(engine, MOCK)
    return engine


def count(engine, table, where="TRUE"):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one()


def dins(folder):
    return [row["din"] for row in CsvData(folder)._rows("drugs")]


def without_status(rows):
    return [{k: v for k, v in row.items() if k != "status"} for row in rows]


# ---------------------------------------------------------
# Loader
# ---------------------------------------------------------

def test_mock_loads_into_every_table(mock_loaded):
    expected = {"site": 6, "tracked_drug": 22, "inventory_snapshot": 132, "usage_record": 132,
                "supplier": 5, "purchase_order": 20, "substitution": 2}
    assert {table: count(mock_loaded, table) for table in expected} == expected
    assert count(mock_loaded, "quarantine") == 0
    assert count(mock_loaded, "raw_snapshot", "source = 'synthetic_csv'") == 6
    assert count(mock_loaded, "ingestion_run", "source = 'synthetic_csv' AND state = 'succeeded'") == 1
    assert count(mock_loaded, "inventory_snapshot", "source_ref NOT LIKE 'snapshot:%'") == 0


def test_reloading_does_not_duplicate(mock_loaded):
    load_folder(mock_loaded, MOCK)
    assert count(mock_loaded, "inventory_snapshot") == 132
    assert count(mock_loaded, "purchase_order") == 20


def test_bad_rows_are_quarantined_not_dropped(engine, tmp_path):
    folder = tmp_path / "day"
    shutil.copytree(BASELINE, folder)
    with open(folder / "inventory.csv", "a") as handle:
        handle.write("A,123,5,2026-11-15\n")          # bad DIN
        handle.write("Z,99000001,5,2026-11-15\n")     # unknown site
        handle.write("A,99000002,-4,2026-11-14\n")    # negative stock

    stats = load_folder(engine, folder)

    assert stats["rows_quarantined"] == 3
    with engine.connect() as conn:
        codes = sorted(row[0] for row in conn.execute(text("SELECT error_code FROM quarantine")))
    assert codes == ["BAD_VALUE", "DIN_FORMAT", "UNKNOWN_SITE"]


def test_progression_keeps_every_day_as_history(engine):
    results = load_progression(engine, DAYS)

    assert [r["day"] for r in results] == [f.name for f in progression_folders(DAYS)]
    assert len(results) == 10
    assert count(engine, "ingestion_run", "source = 'synthetic_csv' AND state = 'succeeded'") == 10
    with engine.connect() as conn:
        days = conn.execute(text("SELECT count(DISTINCT as_of) FROM inventory_snapshot")).scalar_one()
    assert days == 10
    assert count(engine, "inventory_snapshot") == 132 * 10
    assert count(engine, "purchase_order_snapshot") == 20 * 10


# ---------------------------------------------------------
# PostgresData gives the agents what CsvData does
# ---------------------------------------------------------

@pytest.mark.parametrize("din", dins(MOCK))
def test_postgres_data_matches_csv_data(mock_loaded, din):
    csv_data, db_data = CsvData(MOCK), PostgresData(mock_loaded)

    assert db_data.drug(din) == csv_data.drug(din)
    assert db_data.stock(din) == csv_data.stock(din)
    assert db_data.substitutions(din) == csv_data.substitutions(din)
    assert without_status(db_data.orders(din)) == without_status(csv_data.orders(din))


def test_search_matches_csv_data(mock_loaded):
    assert PostgresData(mock_loaded).search_drugs("cyclophosphamide") == CsvData(MOCK).search_drugs("cyclophosphamide")


@pytest.mark.parametrize("day", [f.name for f in progression_folders(DAYS)])
def test_as_of_reads_that_day_of_the_progression(engine, day):
    load_progression(engine, DAYS)

    db_data, csv_day = PostgresData(engine, as_of=date.fromisoformat(day)), CsvData(DAYS / day)
    for din in dins(DAYS / day):
        assert db_data.stock(din) == csv_day.stock(din), f"{din} on {day}"
        assert without_status(db_data.orders(din)) == without_status(csv_day.orders(din)), f"{din} orders on {day}"


def test_progression_story_is_visible_through_as_of(engine):
    load_progression(engine, DAYS)
    site_a = lambda day: next(r for r in PostgresData(engine, as_of=date(2026, 11, day)).stock("99000001") if r["site_id"] == "A")

    assert site_a(18)["on_hand"] == 30     # before the partial transfer
    assert site_a(19)["on_hand"] == 120    # partial transfer of 100 received


def test_orders_follow_the_progression(engine):
    load_progression(engine, DAYS)
    o1001 = lambda day: next(o for o in PostgresData(engine, as_of=date(2026, 11, day)).orders("99000001")
                             if o["order_id"] == "O-1001")

    assert (o1001(15)["expected_date"], o1001(15)["qty"]) == ("2026-11-18", "300")   # before the delay
    assert (o1001(16)["expected_date"], o1001(16)["qty"]) == ("2026-12-07", "300")   # delay applied
    assert (o1001(22)["expected_date"], o1001(22)["qty"]) == ("2026-12-07", "220")   # partial delivery


def test_day_one_email_is_not_double_delayed_after_loading_the_progression(engine):
    """Regression: Nov 15 stock read with Nov 24 orders counted the 3-week delay twice
    (A short by 370 instead of 180)."""

    load_progression(engine, DAYS)
    email = (MOCK / "emails" / "clear.txt").read_text()

    def run(data):
        model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
        return process_email(email, data=data, llm=model, today=date(2026, 11, 15))

    from_db = run(PostgresData(engine, as_of=date(2026, 11, 15)))

    assert from_db["shortfall"] == {"A": 180}
    assert from_db["cover_days"]["A"] == 24
    assert from_db["status"] == "plan_generated"
    assert from_db == run(CsvData(DAYS / "2026-11-15"))


def test_cancelled_orders_do_not_count_as_incoming(mock_loaded):
    before = PostgresData(mock_loaded).orders("99000001")
    with mock_loaded.begin() as conn:
        conn.execute(text("UPDATE purchase_order SET status = 'cancelled' WHERE order_id = 'O-1001'"))

    after = PostgresData(mock_loaded).orders("99000001")

    assert any(o["order_id"] == "O-1001" for o in before)
    assert not any(o["order_id"] == "O-1001" for o in after)


def test_pipeline_gives_same_result_from_database(engine):
    load_folder(engine, BASELINE)

    def run(data):
        model = FakeModel(assessments=[assessment()], plans=[transfer("B", 180)])
        email = (MOCK / "emails" / "clear.txt").read_text()
        return process_email(email, data=data, llm=model, today=date(2026, 11, 15))

    from_db, from_csv = run(PostgresData(engine)), run(CsvData(BASELINE))
    assert from_db == from_csv
    assert from_db["status"] == "plan_generated" and from_db["shortfall"] == {"A": 180}


@pytest.mark.skipif(not READONLY_URL, reason="TEST_DATABASE_URL_READONLY not set")
def test_agent_role_can_read_but_not_write(mock_loaded):
    readonly = create_engine(READONLY_URL)
    assert PostgresData(readonly).drug("99000001")["name"].startswith("Cyclophosphamide")
    with pytest.raises(ProgrammingError, match="permission denied"):
        with readonly.begin() as conn:
            conn.execute(text("UPDATE inventory_snapshot SET on_hand = 0"))
