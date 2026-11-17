"""Simulated-clock demo for the order agent.

    python -m services.agents.order_demo            # offline: rules + scripted supplier replies
    python -m services.agents.order_demo --live     # Bedrock reads replies and decides grey zones
    python -m services.agents.order_demo --trace    # also print every step
    python -m services.agents.order_demo --json     # machine-readable output
    python -m services.agents.order_demo --export   # write apps/web/src/data/orders.json for the website

What happens (mock data, clock starts at the inventory snapshot, Nov 15):
  1. The nightly reorder scan drafts orders for every site heading under 14 days.
  2. A shortage plan's emergency-order option is approved (the cyclophosphamide
     delay from mock/emails/clear.txt), so that order is created already approved.
  3. Procurement approves some drafts and rejects one.
  4. The agent places approved orders through the SIMULATED supplier channel and
     follows them up over four days as scripted supplier replies arrive
     (mock/orders/supplier_replies.json): confirmations, a partial, a delay that
     raises a shortage event, a refusal, a supplier that goes quiet, and a hostile
     reply.

Every run writes (gitignored, under data/orders/):
    audit.jsonl  one JSON line per event and decision (facts, reading, trace, drafts)
    orders.log   timestamped log of every step
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from services.agents.data import CsvData
from services.agents.order_agent import OrderAgent, OrderError, SimulatedSupplierChannel
from services.agents.order_rules import SupplierCatalog, shortage_order_option
from services.agents.order_view import page
from services.agents.risk import calculate_site_risk, cover_days_by_site

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "mock"
LOG_DIR = ROOT / "data" / "orders"
AUDIT_PATH = LOG_DIR / "audit.jsonl"
LOG_PATH = LOG_DIR / "orders.log"
WEB_EXPORT = ROOT / "apps" / "web" / "src" / "data" / "orders.json"

ICONS = {"place": "->", "wait": "..", "chase": ">>", "accept": "ok", "record_delay": "~~",
         "switch_supplier": "<>", "escalate": "!!", "close": "##"}

# Drafts procurement approves in the demo (drug DIN, site), and one it rejects.
APPROVE = [("99000021", "A"), ("99000012", "D"), ("99000011", "A"), ("99000005", "A"), ("99000002", "A")]
REJECT = ("99000013", "A", "Pharmacy is switching to hydromorphone for this ward; fentanyl not needed.")
CHECKS = [
    datetime(2026, 11, 15, 10), datetime(2026, 11, 15, 16), datetime(2026, 11, 16, 12),
    datetime(2026, 11, 16, 18), datetime(2026, 11, 17, 12), datetime(2026, 11, 18, 12),
]


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.unlink(missing_ok=True)
    handler = logging.FileHandler(LOG_PATH, mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def build_agent(live: bool, audit_path=None, audit_writer=None) -> OrderAgent:
    load_dotenv()
    snapshot = datetime.fromisoformat(os.getenv("MOCK_TODAY", "2026-11-15"))
    llm = None
    if live:
        from services.agents.agent import run_agent

        llm = lambda system, task, registry: run_agent(system, task, registry)  # noqa: E731
    return OrderAgent(
        CsvData(MOCK), SupplierCatalog.from_csv(MOCK), [], snapshot, llm=llm,
        channel=SimulatedSupplierChannel.from_file(MOCK / "orders" / "supplier_replies.json"),
        audit_path=audit_path, audit_writer=audit_writer,
    )


def shortage_option(agent: OrderAgent):
    """The order option the signal pipeline adds for the cyclophosphamide delay (21 days)."""

    din, today = "99000001", agent.snapshot.date()
    rows, orders = agent.data.stock(din), agent.data.orders(din)
    _, shortfall = calculate_site_risk(rows, cover_days_by_site(rows, orders, 21, today))
    option, problems = shortage_order_option(din, "Cyclophosphamide 500 mg vial", rows, shortfall,
                                             orders, agent.catalog, today)
    return din, option, problems


def find(agent: OrderAgent, din: str, site: str, status: str = "pending_approval"):
    return next((o for o in agent.orders.values() if o.din == din and o.site_id == site and o.status == status), None)


def run(live: bool = False, audit_path=None) -> dict:
    records: list[dict] = []
    agent = build_agent(live, audit_path, records.append)
    start = datetime(2026, 11, 15, 8)
    out = {"drafted": [], "shortage_option": None, "approvals": [], "checks": []}

    drafted = agent.draft_routine_orders(start)
    out["drafted"] = [o.model_dump(mode="json") | {"total_cost": o.total_cost} for o in drafted]

    din, option, problems = shortage_option(agent)
    out["shortage_option"] = {"option": option.model_dump() if option else None, "problems": problems}
    if option and not problems:
        agent.add_plan_orders(din, option.orders, plan_id="P-demo-cyclo", event_id="SE-demo-cyclo",
                              approved_by="Dana Okafor", role="supply_manager", now=start,
                              reason="Approved emergency-order option for the 3-week cyclophosphamide delay.")

    def approve_drafts(when, pairs):
        for din_, site in pairs:
            order = find(agent, din_, site)
            if order is None:
                continue
            try:
                agent.approve(order.order_id, "Marc Tremblay", "procurement", when)
                out["approvals"].append({"at": when.isoformat(), "order_id": order.order_id, "result": "approved",
                                         "by": "Marc Tremblay (procurement)", "total_cost": order.total_cost})
            except OrderError as error:
                out["approvals"].append({"at": when.isoformat(), "order_id": order.order_id,
                                         "result": "refused", "reason": str(error)})

    approve_drafts(datetime(2026, 11, 15, 9), APPROVE)
    rejected = find(agent, REJECT[0], REJECT[1])
    if rejected:
        agent.reject(rejected.order_id, "Marc Tremblay", "procurement", REJECT[2], datetime(2026, 11, 15, 9))
        out["approvals"].append({"at": "2026-11-15T09:00:00", "order_id": rejected.order_id,
                                 "result": "rejected", "reason": REJECT[2]})

    for when in CHECKS:
        # Replacement orders drafted by the agent wait for a person; approve them before each check.
        replacements = [(o.din, o.site_id) for o in agent.orders.values()
                        if o.status == "pending_approval" and o.replaces]
        approve_drafts(when.replace(minute=0) if when.hour < 12 else when, replacements)
        for action in agent.tick(when):
            out["checks"].append(action.model_dump(mode="json"))

    out["outbox"] = agent.channel.outbox
    out["final"] = {oid: {"status": o.status, "qty": o.qty, "confirmed_qty": o.confirmed_qty, "supplier": agent.catalog.supplier_name(o.supplier_id),
                          "drug": o.drug_name, "site": o.site_id, "expected": str(o.expected_date)}
                    for oid, o in agent.orders.items() if o.status != "pending_approval" or o.replaces}
    out["still_pending"] = sum(1 for o in agent.orders.values() if o.status == "pending_approval")
    out["web"] = page(agent, records, mode="live" if live else "offline",
                      source="services/agents/order_demo.py" + (" --live" if live else ""),
                      last_check=CHECKS[-1], outbox=len(agent.channel.outbox))
    return out


def print_report(out: dict, trace: bool):
    drafted = out["drafted"]
    total = sum(o["total_cost"] for o in drafted)
    print("ORDER AGENT DEMO (offline unless --live). Nothing leaves this machine: the supplier channel is simulated.\n")
    print(f"1. Nightly reorder scan drafted {len(drafted)} orders, ${total:,.0f} in total:")
    for o in drafted[:8]:
        print(f"   {o['order_id']}  {o['drug_name'][:30]:30} site {o['site_id']}  {o['qty']:>4} from "
              f"{o['supplier_id']:18} ${o['total_cost']:>9,.2f}")
    if len(drafted) > 8:
        print(f"   ... and {len(drafted) - 8} more (see data/orders/audit.jsonl)")

    opt = out["shortage_option"]
    print("\n2. Shortage plan, cyclophosphamide 500 mg delayed 3 weeks. Order option added by code:")
    if opt["option"]:
        print(f"   {opt['option']['description']}")
        print(f"   Checks: {'passed' if not opt['problems'] else '; '.join(opt['problems'])}. "
              "Approved by Dana Okafor (supply manager) as part of the plan.")

    print("\n3. People decide:")
    for a in out["approvals"]:
        extra = f" (${a['total_cost']:,.2f})" if "total_cost" in a else f": {a.get('reason', '')}"
        print(f"   {a['at'][5:16].replace('T', ' ')}  {a['order_id']} {a['result']}{extra}")

    print("\n4. The agent follows the orders up:")
    last = None
    for c in out["checks"]:
        if c["at"] != last:
            print(f"\n   -- {c['at'][5:16].replace('T', ' ')} --")
            last = c["at"]
        print(f"   {ICONS.get(c['decision'], '  ')} {c['order_id']} {c['decision'].upper():15} "
              f"[{c['decided_by']}] {c['status_before']} -> {c['status_after']}: {c['reason']}")
        if c.get("sent_via"):
            print(f"      sent to {c['message_to']} (simulated channel)")
        elif c.get("message_draft"):
            print(f"      draft for {c['message_to']}: {c['message_draft'][:110]}")
        if c.get("new_order"):
            n = c["new_order"]
            print(f"      new draft {n['order_id']}: {n['qty']} from {n['supplier_id']}, waiting for approval")
        if c.get("raised_event"):
            print(f"      shortage event raised: {c['raised_event']['reason']}")
        for flag in c.get("flags", []):
            print(f"      flag: {flag}")
        if trace:
            for line in c["trace"]:
                print(f"        . {line}")

    print("\n5. Where every order ended up:")
    for oid, f in out["final"].items():
        confirmed = f.get("confirmed_qty")
        qty = f"{confirmed} of {f['qty']}" if confirmed is not None and confirmed < f["qty"] else str(f["qty"])
        print(f"   {oid}  {f['status']:10} {f['drug'][:30]:30} site {f['site']}  {qty:>10} from {f['supplier']}"
              f"  (due {f['expected']})")
    print(f"\n   {out['still_pending']} draft order(s) still waiting for a person.")
    print(f"   Messages through the simulated channel: {len(out['outbox'])}. Audit: {AUDIT_PATH.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Order agent demo on a simulated clock")
    parser.add_argument("--live", action="store_true", help="Use Bedrock for reading replies and grey-zone decisions")
    parser.add_argument("--trace", action="store_true", help="Print every step")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the report")
    parser.add_argument("--export", nargs="?", const=str(WEB_EXPORT), metavar="PATH",
                        help=f"Write the website's order data (default {WEB_EXPORT.relative_to(ROOT)})")
    args = parser.parse_args(argv)
    setup_logging()
    out = run(args.live, AUDIT_PATH)
    if args.export:
        Path(args.export).write_text(json.dumps(out["web"], indent=1, default=str) + "\n")
        print(f"Wrote {args.export}: {len(out['web']['orders'])} orders, {len(out['web']['events'])} events.")
        return 0
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print_report(out, args.trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
