"""Run the order agent on the database instead of CSV files.

OrderAgent (order_agent.py) is used unchanged. This module feeds it and stores
what it does:

- Supplier catalog: supplier + supplier_catalog tables (load them from
  mock/suppliers.csv and mock/supplier_catalog.csv with `load-catalog`).
- Orders: purchase_order rows the agent manages (kind 'routine' or 'shortage').
  Orders loaded from the hospital feed (kind NULL) are only read, as incoming stock.
- Shortage orders: every order line of an *approved* plan option that has no
  purchase order yet becomes one (status approved, linked by action_id).
- Record: every event and decision goes to audit_log (actor "order-agent") in
  the same transaction as the purchase_order changes. Reminder counts and
  escalations are rebuilt from those entries on the next run.
- Delays that leave a site short raise a supply event. With a model configured
  (--live) it is planned by the signal pipeline right away (agent.process_event)
  and saved like any other plan; otherwise it is reported for a person.

Nothing reaches a real supplier: placing and reminders go through the
simulated channel (optionally playing back scripted replies with --replies).

  python -m services.agents.order_db load-catalog
  python -m services.agents.order_db scan --as-of 2026-11-15
  python -m services.agents.order_db list
  python -m services.agents.order_db approve PO-1a2b3c4d --by "Sam Lee" --role procurement
  python -m services.agents.order_db reject  PO-1a2b3c4d --by "Sam Lee" --role procurement --note "Not needed"
  python -m services.agents.order_db run --now 2026-11-16T12:00 --as-of 2026-11-15 --replies mock/orders/supplier_replies.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ProgrammingError

from services.agents.order_agent import OrderAgent, OrderError, SimulatedSupplierChannel
from services.agents.order_rules import SupplierCatalog
from services.agents.order_schemas import OrderAction, PurchaseOrder
from services.agents.schemas import OrderLine

logger = logging.getLogger(__name__)

ACTOR = "order-agent"
ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "mock"
AGENT_STATUSES = ("pending_approval", "approved", "placed", "in_transit", "delayed", "delivered", "cancelled")


def _new_id() -> str:
    return f"PO-{uuid.uuid4().hex[:8]}"


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


# ---------------------------------------------------------
# Supplier catalog
# ---------------------------------------------------------

def load_catalog(engine: Engine, folder: Path = MOCK) -> dict:
    """Upsert suppliers and their catalog from CSV. Rows for drugs we don't track are skipped."""

    folder = Path(folder)
    with (folder / "suppliers.csv").open(newline="", encoding="utf-8-sig") as handle:
        suppliers = list(csv.DictReader(handle))
    with (folder / "supplier_catalog.csv").open(newline="", encoding="utf-8-sig") as handle:
        offers = list(csv.DictReader(handle))
    counts = {"suppliers": 0, "offers": 0, "skipped": 0}
    with engine.begin() as conn:
        tracked = {row.din for row in conn.execute(text("SELECT din FROM tracked_drug"))}
        for row in suppliers:
            conn.execute(text("""
                INSERT INTO supplier (supplier_id, name, channel, active, on_time_rate, contact)
                VALUES (:supplier_id, :name, :channel, :active, :on_time_rate, :contact)
                ON CONFLICT (supplier_id) DO UPDATE SET name = EXCLUDED.name, channel = EXCLUDED.channel,
                    active = EXCLUDED.active, on_time_rate = EXCLUDED.on_time_rate, contact = EXCLUDED.contact
            """), {**row, "active": row.get("active", "true").lower() != "false",
                   "on_time_rate": float(row["on_time_rate"])})
            counts["suppliers"] += 1
        for row in offers:
            if row["din"] not in tracked:
                counts["skipped"] += 1
                continue
            conn.execute(text("""
                INSERT INTO supplier_catalog (supplier_id, din, unit_cost, pack_size, min_order_qty,
                                              lead_time_days, availability, source_ref)
                VALUES (:supplier_id, :din, :unit_cost, :pack_size, :min_order_qty, :lead_time_days,
                        :availability, :source_ref)
                ON CONFLICT (supplier_id, din) DO UPDATE SET unit_cost = EXCLUDED.unit_cost,
                    pack_size = EXCLUDED.pack_size, min_order_qty = EXCLUDED.min_order_qty,
                    lead_time_days = EXCLUDED.lead_time_days, availability = EXCLUDED.availability,
                    source_ref = EXCLUDED.source_ref
            """), {**row, "source_ref": f"{folder.name}/supplier_catalog.csv"})
            counts["offers"] += 1
    return counts


def load_catalog_from_db(engine: Engine) -> SupplierCatalog | None:
    """None when the catalog table is missing or empty (no order options then)."""

    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT c.supplier_id, s.name AS supplier_name, c.din, c.unit_cost, c.pack_size,
                       c.min_order_qty, c.lead_time_days, c.availability,
                       coalesce(s.on_time_rate, 0.9) AS on_time_rate, s.channel, s.contact, s.active
                FROM supplier_catalog c JOIN supplier s USING (supplier_id)
            """)).all()
    except ProgrammingError:
        return None
    if not rows:
        return None
    from services.agents.order_schemas import SupplierOffer

    return SupplierCatalog([
        SupplierOffer(**{**dict(row._mapping), "unit_cost": float(row.unit_cost),
                         "on_time_rate": float(row.on_time_rate)})
        for row in rows
    ])


# ---------------------------------------------------------
# Reading the agent's orders and state
# ---------------------------------------------------------

def managed_orders(engine: Engine) -> list[PurchaseOrder]:
    """Orders the agent drafted or placed, with reminder/escalation state from audit_log."""

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT o.*, t.name AS drug_name
            FROM purchase_order o LEFT JOIN tracked_drug t USING (din)
            WHERE o.kind IS NOT NULL AND o.status = ANY(CAST(:statuses AS text[]))
            ORDER BY o.created_at, o.order_id
        """), {"statuses": list(AGENT_STATUSES)}).all()
        state = {row.object: row.detail for row in conn.execute(text("""
            SELECT DISTINCT ON (object) object, detail -> 'order' AS detail
            FROM audit_log
            WHERE actor = :actor AND detail -> 'order' IS NOT NULL
            ORDER BY object, audit_id DESC
        """), {"actor": ACTOR})}

    orders = []
    for row in rows:
        saved = state.get(row.order_id) or {}
        orders.append(PurchaseOrder(
            order_id=row.order_id, kind=row.kind, din=row.din, drug_name=row.drug_name,
            site_id=row.site_id, supplier_id=row.supplier_id, qty=row.qty,
            unit_cost=float(row.unit_cost or 0), status=row.status,
            created_at=_naive_utc(row.created_at), needed_by=row.needed_by,
            expected_date=row.expected_date, promised_date=row.promised_date,
            placed_at=_naive_utc(row.placed_at), confirmed_qty=row.confirmed_qty or 0,
            received_qty=row.received_qty or 0, approved_by=row.approved_by,
            approved_role=row.approved_role, plan_id=row.plan_id, event_id=row.event_id,
            replaces=row.replaces, action_id=row.action_id, reason=row.reason or "",
            chases=saved.get("chases", 0),
            last_chased_at=saved.get("last_chased_at"),
            escalated_at=saved.get("escalated_at"),
        ))
    return orders


def last_check_time(engine: Engine) -> datetime | None:
    with engine.connect() as conn:
        value = conn.execute(text("""
            SELECT max(detail ->> 'at') FROM audit_log
            WHERE actor = :actor AND detail ->> 'kind' = 'check'
        """), {"actor": ACTOR}).scalar_one()
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------
# Writing: orders and audit entries in one transaction
# ---------------------------------------------------------

UPSERT_ORDER = text("""
    INSERT INTO purchase_order (order_id, din, site_id, supplier_id, qty, unit_cost, status, created_at,
                                promised_date, expected_date, delivered_at, event_id, plan_id, source_ref,
                                kind, approved_by, approved_role, placed_at, needed_by, confirmed_qty,
                                received_qty, action_id, replaces, reason)
    VALUES (:order_id, :din, :site_id, :supplier_id, :qty, :unit_cost, :status, :created_at,
            :promised_date, :expected_date, :delivered_at, :event_id, :plan_id, 'order-agent',
            :kind, :approved_by, :approved_role, :placed_at, :needed_by, :confirmed_qty,
            :received_qty, :action_id, :replaces, :reason)
    ON CONFLICT (order_id) DO UPDATE SET
        status = EXCLUDED.status, qty = EXCLUDED.qty, unit_cost = EXCLUDED.unit_cost,
        promised_date = EXCLUDED.promised_date, expected_date = EXCLUDED.expected_date,
        delivered_at = coalesce(purchase_order.delivered_at, EXCLUDED.delivered_at),
        approved_by = EXCLUDED.approved_by, approved_role = EXCLUDED.approved_role,
        placed_at = EXCLUDED.placed_at, confirmed_qty = EXCLUDED.confirmed_qty,
        received_qty = EXCLUDED.received_qty, reason = EXCLUDED.reason
""")


class Recorder:
    """Collects the agent's audit records, then writes them with the orders."""

    def __init__(self):
        self.records: list[dict] = []

    def __call__(self, record: dict):
        self.records.append(record)


def persist(engine: Engine, agent: OrderAgent, recorder: Recorder, now: datetime) -> None:
    with engine.begin() as conn:
        for order in agent.orders.values():
            values = order.model_dump()
            values["created_at"] = order.created_at.replace(tzinfo=timezone.utc)
            values["placed_at"] = order.placed_at.replace(tzinfo=timezone.utc) if order.placed_at else None
            values["delivered_at"] = now.date() if order.status == "delivered" else None
            conn.execute(UPSERT_ORDER, values)
        for record in recorder.records:
            order = agent.orders.get(record.get("object"))
            refs = [ref for ref in (record.get("object"), order.plan_id if order else None) if ref]
            conn.execute(text("""
                INSERT INTO audit_log (actor, actor_type, action, object, detail, refs, at)
                VALUES (:actor, :actor_type, :action, :object, CAST(:detail AS jsonb), :refs, :at)
            """), {"actor": record["actor"], "actor_type": record["actor_type"], "action": record["action"],
                   "object": record.get("object"), "detail": json.dumps(record, default=str), "refs": refs,
                   # On the agent's clock (the simulated day), like plan approvals.
                   "at": _naive_utc(now).replace(tzinfo=timezone.utc)})
    recorder.records.clear()


# ---------------------------------------------------------
# Building the agent and the jobs
# ---------------------------------------------------------

def build_agent(engine: Engine, data, llm=None, channel: SimulatedSupplierChannel | None = None):
    from services.agents.followup_db import stock_snapshot_time

    catalog = load_catalog_from_db(engine)
    if catalog is None:
        raise RuntimeError("No supplier catalog in the database. Run: python -m services.agents.order_db load-catalog")
    recorder = Recorder()
    agent = OrderAgent(data, catalog, managed_orders(engine), stock_snapshot_time(engine, data),
                       llm=llm, channel=channel, audit_writer=recorder, new_id=_new_id)
    agent.last_check = last_check_time(engine)
    return agent, recorder


def sync_plan_orders(engine: Engine, agent: OrderAgent, now: datetime) -> list[PurchaseOrder]:
    """Approved order options of plans -> purchase orders (once per plan action)."""

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT a.action_id, a.plan_id, p.event_id, e.din, a.to_site, a.units, a.supplier_id,
                   d.actor, d.actor_role
            FROM decision d
            JOIN plan p ON p.plan_id = d.plan_id
            JOIN supply_event e ON e.event_id = p.event_id
            JOIN plan_action a ON a.plan_id = d.plan_id AND a.option_index = d.option_index
            WHERE d.decision = 'approved' AND p.status = 'approved' AND a.type = 'order'
              AND NOT EXISTS (SELECT 1 FROM purchase_order o WHERE o.action_id = a.action_id)
            ORDER BY a.action_id
        """)).all()
    created = []
    for row in rows:
        [order] = agent.add_plan_orders(
            row.din, [OrderLine(supplier_id=row.supplier_id, site_id=row.to_site, units=row.units)],
            row.plan_id, row.event_id, row.actor, row.actor_role, now, order_ids=[f"PO-{row.action_id}"],
        ) or [None]
        if order is not None:
            order.action_id = row.action_id
            created.append(order)
    return created


def scan(engine: Engine, data, now: datetime) -> list[PurchaseOrder]:
    """Nightly reorder scan: draft orders for sites heading under their safety stock."""

    agent, recorder = build_agent(engine, data)
    drafted = agent.draft_routine_orders(_naive_utc(now))
    persist(engine, agent, recorder, now)
    return drafted


def approve_order(engine: Engine, order_id: str, by: str, role: str, data, now: datetime,
                  note: str | None = None) -> PurchaseOrder:
    agent, recorder = build_agent(engine, data)
    order = agent.approve(order_id, by, role, _naive_utc(now), note)
    persist(engine, agent, recorder, now)
    return order


def reject_order(engine: Engine, order_id: str, by: str, role: str, note: str, data, now: datetime) -> PurchaseOrder:
    agent, recorder = build_agent(engine, data)
    order = agent.reject(order_id, by, role, note, _naive_utc(now))
    persist(engine, agent, recorder, now)
    return order


def run_orders(engine: Engine, data, now: datetime, channel: SimulatedSupplierChannel | None = None,
               llm=None) -> tuple[list[OrderAction], list[dict]]:
    """Create orders from approved plans, then check every open order at `now`.
    Returns (actions, results of planning raised supply events)."""

    agent, recorder = build_agent(engine, data, llm, channel)
    naive_now = _naive_utc(now)
    sync_plan_orders(engine, agent, naive_now)
    actions = agent.tick(naive_now)

    planned = []
    for action in actions:
        if not action.raised_event:
            continue
        planned.append(plan_raised_event(engine, data, action, agent.snapshot.date(), llm))
    # Orders are written after planning, so the planner saw the supplier's original
    # date plus the reported delay (as for a supplier email), not the new date twice.
    persist(engine, agent, recorder, now)
    return actions, planned


def plan_raised_event(engine: Engine, data, action: OrderAction, today: date, llm) -> dict:
    """A delay that leaves a site short becomes a supply event for the signal pipeline."""

    event = {k: action.raised_event[k] for k in ("drug_din", "drug_name", "delay_weeks", "confidence",
                                                  "next_action", "reason")}
    if llm is None:
        return {"order_id": action.order_id, "status": "needs_planning", "event": event,
                "note": "No model configured; run with --live to plan it, or a person plans it."}
    from services.agents.agent import process_event
    from services.agents.results_store import save_pipeline_result
    from services.agents.schemas import SupplyEvent

    result = process_event(SupplyEvent(**event), data=data, llm=llm, today=today)
    saved = save_pipeline_result(engine, result, source_ref=action.order_id, actor=ACTOR,
                                 source_type="supplier_email", kind="delay")
    return {"order_id": action.order_id, "status": result["status"], **saved}


def list_orders(engine: Engine) -> list[dict]:
    return [
        {"order_id": o.order_id, "status": o.status, "kind": o.kind, "drug": o.drug_name, "site": o.site_id,
         "supplier": o.supplier_id, "qty": o.qty, "total_cost": o.total_cost,
         "expected": o.expected_date.isoformat() if o.expected_date else None, "reason": o.reason}
        for o in managed_orders(engine)
    ]


# ---------------------------------------------------------
# Command line
# ---------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Order agent on the database")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("load-catalog", help="Load mock/suppliers.csv and mock/supplier_catalog.csv")
    sub.add_parser("list", help="Orders the agent manages")
    for name in ("scan", "approve", "reject", "run"):
        p = sub.add_parser(name)
        p.add_argument("--as-of", type=date.fromisoformat, help="Stock snapshot day (default: latest)")
        p.add_argument("--now", type=datetime.fromisoformat, default=None, help="Clock time (default: now, UTC)")
        if name in ("approve", "reject"):
            p.add_argument("order_id")
            p.add_argument("--by", required=True)
            p.add_argument("--role", required=True, choices=["procurement", "supply_manager"])
            p.add_argument("--note", required=(name == "reject"))
        if name == "run":
            p.add_argument("--replies", help="Scripted supplier replies (mock/orders/supplier_replies.json)")
            p.add_argument("--live", action="store_true", help="Bedrock reads replies, decides, plans raised events")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ingestion.lineage import get_engine
    from services.agents.db_data import PostgresData

    engine = get_engine()
    now = getattr(args, "now", None) or datetime.now(timezone.utc)
    try:
        if args.command == "load-catalog":
            result = load_catalog(engine)
        elif args.command == "list":
            result = list_orders(engine)
        else:
            data = PostgresData.from_env(as_of=args.as_of)
            if args.command == "scan":
                result = [o.model_dump(mode="json") | {"total_cost": o.total_cost} for o in scan(engine, data, now)]
            elif args.command == "approve":
                result = approve_order(engine, args.order_id, args.by, args.role, data, now, args.note).model_dump(mode="json")
            elif args.command == "reject":
                result = reject_order(engine, args.order_id, args.by, args.role, args.note, data, now).model_dump(mode="json")
            else:
                llm = None
                if args.live:
                    from services.agents.agent import run_agent

                    llm = lambda system, task, registry: run_agent(system, task, registry)  # noqa: E731
                channel = SimulatedSupplierChannel.from_file(args.replies) if args.replies else None
                actions, planned = run_orders(engine, data, now, channel, llm)
                for action in actions:
                    print(f"{action.order_id}: {action.decision.upper()} by {action.decided_by} | {action.reason}")
                result = {"checked": len(actions), "raised_events": planned}
    except OrderError as error:
        print(f"Not allowed: {error}", file=sys.stderr)
        return 2
    except ProgrammingError as error:
        if "does not exist" in str(error.orig):
            print("The database is missing tables or columns this needs. Run: .venv/bin/alembic upgrade head",
                  file=sys.stderr)
            return 3
        raise
    print(json.dumps(result, indent=2, default=str))
    print("Nothing reached a real supplier: orders and reminders go through the simulated channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
