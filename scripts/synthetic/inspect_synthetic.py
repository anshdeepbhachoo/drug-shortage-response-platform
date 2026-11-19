"""Inspect the platform's synthetic data through existing agent loaders/tools.

Examples (PowerShell, from repo root):

  python -m scripts.synthetic.make_mock_data
  python -m scripts.synthetic.generate_progression
  python -m scripts.synthetic.inspect_synthetic summary
  python -m scripts.synthetic.inspect_synthetic day 2026-11-19
  python -m scripts.synthetic.inspect_synthetic day 5 --din 99000001
  python -m scripts.synthetic.inspect_synthetic risk --day 4 --din 99000001
  python -m scripts.synthetic.inspect_synthetic spare --day 4 --site B --din 99000001 --keep-days 19
  python -m scripts.synthetic.inspect_synthetic generate
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from scripts.synthetic.generate_progression import FOCUS_DIN, NUM_DAYS, START, run_progression
from scripts.synthetic.make_mock_data import generate as generate_mock
from services.agents.data.data import CsvData
from services.agents.rules.risk import calculate_site_risk, cover_days_by_site
from services.agents.agents.tools import build_tool_registries

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "synthetic" / "mock"
PROGRESSION = ROOT / "data" / "synthetic" / "progression"
PROTECTED_EMAILS = ("clear.txt", "ambiguous.txt", "hostile.txt", "sneaky.txt")


def resolve_day(value: str) -> Path:
    """Accept YYYY-MM-DD or day number 1..N relative to progression start."""
    days_root = PROGRESSION / "days"
    if value.isdigit():
        day_num = int(value)
        if day_num < 1 or day_num > NUM_DAYS:
            raise SystemExit(f"Day number must be 1..{NUM_DAYS}, got {day_num}")
        as_of = START + timedelta(days=day_num - 1)
        path = days_root / as_of.isoformat()
    else:
        date.fromisoformat(value)  # validate
        path = days_root / value
    if not path.exists():
        raise SystemExit(
            f"Missing snapshot {path}. Run: python -m scripts.synthetic.generate_progression"
        )
    return path


def cmd_summary(_: argparse.Namespace) -> None:
    sites = list(csv.DictReader((MOCK / "sites.csv").open(encoding="utf-8-sig")))
    drugs = list(csv.DictReader((MOCK / "drugs.csv").open(encoding="utf-8-sig")))
    inv = list(csv.DictReader((MOCK / "inventory.csv").open(encoding="utf-8-sig")))
    orders = list(csv.DictReader((MOCK / "open_orders.csv").open(encoding="utf-8-sig")))
    emails = sorted(p.name for p in (MOCK / "emails").glob("*.txt"))
    print("=== Current-day data/synthetic/mock/ ===")
    print(f"hospitals: {len(sites)}")
    print(f"medications: {len(drugs)}")
    print(f"inventory rows: {len(inv)}")
    print(f"open orders: {len(orders)}")
    print(f"emails: {len(emails)}")
    print(f"protected emails present: {all((MOCK / 'emails' / n).exists() for n in PROTECTED_EMAILS)}")
    print(f"followup fixtures present: {(MOCK / 'followup' / 'transfer_requests.csv').exists()}")
    print()
    print("=== Progression ===")
    days = sorted((PROGRESSION / "days").glob("*")) if (PROGRESSION / "days").exists() else []
    print(f"snapshots: {len(days)}")
    if (PROGRESSION / "REPORT.md").exists():
        print(f"report: {PROGRESSION / 'REPORT.md'}")
    print()
    print("Tip: python -m scripts.synthetic.inspect_synthetic day 5 --din 99000001")


def cmd_day(args: argparse.Namespace) -> None:
    folder = resolve_day(args.day) if args.day != "current" else MOCK
    label = "data/synthetic/mock/ (current day)" if args.day == "current" else folder.name
    data = CsvData(folder)
    din = args.din
    print(f"=== Snapshot: {label} | DIN {din} ===")
    drug = data.drug(din)
    if drug:
        print(f"drug: {drug['name']} ({drug['criticality']})")
    for row in data.stock(din):
        dos = row["days_of_supply"]
        print(
            f"  {row['site_id']} {row['site']}: on_hand={row['on_hand']} "
            f"daily_use={row['daily_use']} days_supply={dos}"
        )
    print("orders:")
    for order in data.orders(din):
        print(
            f"  {order['order_id']} site={order['site_id']} qty={order['qty']} "
            f"expected={order['expected_date']} status={order['status']}"
        )
    subs = data.substitutions(din)
    if subs:
        print("substitutions (review-required records):")
        for row in subs:
            print(f"  -> {row['substitute_din']}: {row['note']}")


def _horizon_for_snapshot(folder: Path, as_of: date, din: str) -> dict[str, int]:
    data = CsvData(folder)
    stock = data.stock(din)
    orders = data.orders(din)
    if as_of == START:
        return cover_days_by_site(stock, orders, delay_days=21, today=as_of)
    focus = next((row for row in orders if row["order_id"] == "O-1001"), None)
    if focus and din == FOCUS_DIN:
        due = date.fromisoformat(focus["expected_date"])
        horizon = max(1, (due - as_of).days)
        return {row["site_id"]: horizon for row in stock}
    # Fallback: per-site cover with no extra delay.
    return cover_days_by_site(stock, orders, delay_days=1, today=as_of)


def cmd_risk(args: argparse.Namespace) -> None:
    folder = resolve_day(args.day)
    as_of = date.fromisoformat(folder.name)
    data = CsvData(folder)
    stock = data.stock(args.din)
    cover = _horizon_for_snapshot(folder, as_of, args.din)
    at_risk, shortfall = calculate_site_risk(stock, cover)
    print(f"=== Risk for {args.din} on {folder.name} ===")
    if as_of == START:
        print("horizon: agent-style per-site cover + hypothetical 21-day delay")
    elif args.din == FOCUS_DIN:
        print("horizon: SHARED disruption horizon until delayed O-1001 (demo lens)")
    print(f"at_risk: {at_risk}")
    print(f"shortfall: {shortfall}")
    for row in stock:
        print(
            f"  {row['site_id']}: on_hand={row['on_hand']} daily_use={row['daily_use']} "
            f"cover={cover[row['site_id']]} days_supply={row['days_of_supply']}"
        )


def cmd_spare(args: argparse.Namespace) -> None:
    folder = resolve_day(args.day)
    data = CsvData(folder)
    _, planning = build_tool_registries(data)
    result = planning.execute(
        "get_spare_units",
        {
            "din": args.din,
            "site_id": args.site,
            "keep_days": args.keep_days,
        },
    )
    print(f"=== Spare units on {folder.name} ===")
    for key, value in result.items():
        print(f"  {key}: {value}")


def cmd_generate(_: argparse.Namespace) -> None:
    generate_mock(MOCK)
    run_progression(MOCK, PROGRESSION)
    print("Current-day data/synthetic/mock/ and progression snapshots refreshed.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sum = sub.add_parser("summary", help="Show dataset counts and status")
    p_sum.set_defaults(func=cmd_summary)

    p_day = sub.add_parser("day", help="Inspect a snapshot via CsvData")
    p_day.add_argument("day", help="YYYY-MM-DD, day number 1-10, or 'current'")
    p_day.add_argument("--din", default=FOCUS_DIN)
    p_day.set_defaults(func=cmd_day)

    p_risk = sub.add_parser("risk", help="Run deterministic risk.py on a day")
    p_risk.add_argument("--day", required=True, help="YYYY-MM-DD or day number")
    p_risk.add_argument("--din", default=FOCUS_DIN)
    p_risk.set_defaults(func=cmd_risk)

    p_spare = sub.add_parser("spare", help="Run get_spare_units tool on a day")
    p_spare.add_argument("--day", required=True)
    p_spare.add_argument("--site", required=True)
    p_spare.add_argument("--din", default=FOCUS_DIN)
    p_spare.add_argument("--keep-days", type=float, default=14)
    p_spare.set_defaults(func=cmd_spare)

    p_gen = sub.add_parser("generate", help="Regenerate data/synthetic/mock/ + progression")
    p_gen.set_defaults(func=cmd_generate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
