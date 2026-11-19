"""Shared by the state builders: paths, UI vocabularies, row helpers and the scenario clock."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC = ROOT / "data" / "synthetic"
EMAILS = SYNTHETIC / "mock" / "emails"

ROLE_OF_APPROVER = {"supply_manager": "supply", "pharmacy_manager": "pharmacist", "pharmacist": "pharmacist",
                    "procurement": "procurement"}
EVENT_KIND = {"delay": "delay", "shortage": "shortage", "usage_spike": "usage-spike", "discontinuation": "shortage"}
EVENT_SOURCE = {"supplier_email": "supplier-email", "shortage_report": "hc-shortage-api", "site_report": "site-report",
                "stock_watch": "site-report", "manual": "site-report"}
EVENT_STATUS = {"open": "open", "monitoring": "monitoring", "mitigated": "mitigated", "closed": "mitigated"}


def _rows(conn, sql: str, **params) -> list[dict]:
    return [dict(row._mapping) for row in conn.execute(text(sql), params)]


def loaded_days(engine: Engine) -> list[date]:
    """Days whose hospital data has been ingested."""

    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT DISTINCT as_of FROM inventory_snapshot ORDER BY as_of"))]


def scenario_days() -> list[date]:
    """Every day of the simulation, whether or not its data has arrived yet."""

    from ingestion.synthetic_loader import progression_folders

    try:
        return [date.fromisoformat(folder.name) for folder in progression_folders()]
    except FileNotFoundError:
        return []


def available_days(engine: Engine) -> list[date]:
    return sorted(set(loaded_days(engine)) | set(scenario_days()))


def scenario_start(as_of: date) -> datetime:
    return datetime.combine(as_of, time(8, 0))


def minutes_from(start: datetime, moment: datetime | None) -> int:
    if moment is None:
        return 0
    if moment.tzinfo:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return round((moment - start).total_seconds() / 60)
