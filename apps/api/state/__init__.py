"""Build the web UI's state from the database, in the shapes apps/web/src/lib/types.ts uses.

The UI was written against a scripted demo (apps/web/src/data/mock.ts). Rather
than rewrite every page, the API hands it the same shapes filled with real
data: sites, drugs, stock lines with dated orders, supply events, one plan card
per plan option, audit entries, emails, and ingestion runs.

Dates: the UI counts days and minutes from a "scenario start". Here that is
the stock day being viewed (as_of), so day 0 is that day and orders, run-out
dates and audit times line up with it.

The package: common (paths, vocabularies, helpers), stock, plans (events, plan cards,
emails), audit (history, data sources) and followups (follow-up, notifications).
build_state puts them together in one response.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from apps.api.state.audit import audit_entries, ingestion, sources
from apps.api.state.common import EMAILS, _rows, available_days, loaded_days, scenario_start
from apps.api.state.followups import followups, notifications
from apps.api.state.plans import _inbox_for, _plans_raw, emails, plan_cards, supply_events
from apps.api.state.stock import platform_orders, sites_and_drugs, stock_lines
from services.data.db_data import PostgresData

__all__ = ["EMAILS", "build_state"]


def build_state(engine: Engine, as_of: date | None = None) -> dict:
    days = available_days(engine)
    if not days:
        raise LookupError("No scenario days and no stock loaded. Run: python -m ingestion.synthetic_loader --progression")
    if as_of is None:
        # Open where the user left off: the day after the last one run (or the last day).
        with engine.connect() as conn:
            last = conn.execute(text("SELECT max(object) FROM audit_log WHERE action = 'Daily run'")).scalar()
        later = [d for d in days if last and d.isoformat() > last]
        as_of = later[0] if later else (days[-1] if last else days[0])
    start = scenario_start(as_of)
    data = PostgresData(engine, as_of=as_of)

    with engine.connect() as conn:
        sites, drugs = sites_and_drugs(conn)
        names = {d["id"]: d["name"] for d in drugs}
        platform = platform_orders(conn)
        stock = stock_lines(conn, data, as_of, drugs, platform)
        raw_plans = _plans_raw(conn)
        events = supply_events(conn, start, raw_plans, names)
        tracked = followups(conn, names, {site["id"]: site["name"] for site in sites})
        state = {
            "followups": tracked,
            "notifications": notifications(conn, tracked),
            "asOf": as_of.isoformat(),
            "days": [d.isoformat() for d in days],
            # Days whose data has arrived; before that the stock shown is the latest earlier day (or none).
            "loadedDays": [d.isoformat() for d in loaded_days(engine)],
            "scenarioStart": start.isoformat(),
            "sites": sites,
            "drugs": drugs,
            "stock": stock,
            "events": events,
            "plans": plan_cards(raw_plans, as_of, platform),
            "emails": emails(conn, start, names),
            "audit": audit_entries(conn, start, {site["id"]: site["name"] for site in sites}),
            "ingestion": ingestion(conn),
            "daysRun": [r["object"] for r in _rows(conn, "SELECT DISTINCT object FROM audit_log WHERE action = 'Daily run' ORDER BY object")],
            # Each day's report (the latest run of that day), for the Home page.
            "dayReports": {r["object"]: r["detail"] for r in _rows(conn, """
                SELECT DISTINCT ON (object) object, detail FROM audit_log
                WHERE action = 'Daily run' ORDER BY object, audit_id DESC""")},
            "inbox": _inbox_for(as_of),
            "sources": sources(conn, events, raw_plans, stock),
        }
    return state
