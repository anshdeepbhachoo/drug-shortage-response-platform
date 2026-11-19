"""Generate a deterministic multi-day synthetic progression from data/synthetic/mock/.

Does NOT overwrite data/synthetic/mock/. Writes snapshots to data/synthetic/progression/days/<date>/
with the same CSV schemas expected by services.agents.data.data.CsvData.

Usage:
  python -m scripts.synthetic.generate_progression
  python -m scripts.synthetic.generate_progression --source data/synthetic/mock --out data/synthetic/progression
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from services.agents.data.data import CsvData
from services.agents.rules.risk import calculate_site_risk, cover_days_by_site

START = date(2026, 11, 15)
NUM_DAYS = 10
FOCUS_DIN = "99000001"
REQUIRED = (
    "drugs.csv",
    "sites.csv",
    "usage.csv",
    "inventory.csv",
    "open_orders.csv",
    "substitutions.csv",
)


@dataclass
class World:
    sites: list[dict]
    drugs: list[dict]
    substitutions: list[dict]
    inventory: dict[tuple[str, str], int]
    usage: dict[tuple[str, str], float]
    orders: list[dict]
    ledger: list[dict] = field(default_factory=list)

    def log(self, day: int, as_of: date, event_type: str, **details) -> None:
        self.ledger.append(
            {"day": day, "date": as_of.isoformat(), "type": event_type, **details}
        )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="\n", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_world(source: Path) -> World:
    for name in REQUIRED:
        if not (source / name).exists():
            raise FileNotFoundError(f"Missing {source / name}")

    inv_rows = read_csv(source / "inventory.csv")
    usage_rows = read_csv(source / "usage.csv")
    return World(
        sites=read_csv(source / "sites.csv"),
        drugs=read_csv(source / "drugs.csv"),
        substitutions=read_csv(source / "substitutions.csv"),
        inventory={
            (row["site_id"], row["din"]): int(row["on_hand"]) for row in inv_rows
        },
        usage={
            (row["site_id"], row["din"]): float(row["avg_daily_use"])
            for row in usage_rows
        },
        orders=read_csv(source / "open_orders.csv"),
    )


def consume_one_day(world: World, day: int, as_of: date) -> None:
    stockouts = []
    for key, daily in list(world.usage.items()):
        before = world.inventory[key]
        requested = max(0, int(daily))
        used = min(before, requested)
        world.inventory[key] = before - used
        if used < requested:
            stockouts.append(
                {"site_id": key[0], "din": key[1], "requested": requested, "used": used}
            )
    world.log(
        day,
        as_of,
        "consume",
        note="applied avg_daily_use (capped at on_hand)",
        stockouts=stockouts,
    )


def apply_delay_order(world: World, day: int, as_of: date, order_id: str, new_date: str) -> None:
    for order in world.orders:
        if order["order_id"] == order_id:
            previous = order["expected_date"]
            order["expected_date"] = new_date
            world.log(
                day,
                as_of,
                "order_delay",
                order_id=order_id,
                previous_expected_date=previous,
                new_expected_date=new_date,
            )
            return
    raise KeyError(f"Unknown order_id {order_id}")


def apply_transfer_received(
    world: World,
    day: int,
    as_of: date,
    *,
    din: str,
    from_site: str,
    to_site: str,
    units: int,
) -> None:
    if units <= 0:
        raise ValueError("transfer units must be positive")
    src = (from_site, din)
    dst = (to_site, din)
    if world.inventory[src] < units:
        raise ValueError(
            f"Transfer exceeds stock on day {day}: {from_site} has "
            f"{world.inventory[src]}, needs {units}"
        )
    world.inventory[src] -= units
    world.inventory[dst] += units
    world.log(
        day,
        as_of,
        "transfer_received",
        din=din,
        from_site=from_site,
        to_site=to_site,
        units=units,
    )


def apply_delivery(
    world: World,
    day: int,
    as_of: date,
    *,
    order_id: str,
    site_id: str,
    din: str,
    units: int,
) -> None:
    if units <= 0:
        raise ValueError("delivery units must be positive")
    order = next((row for row in world.orders if row["order_id"] == order_id), None)
    if order is None:
        raise KeyError(order_id)
    if order["site_id"] != site_id:
        raise ValueError(
            f"Delivery site {site_id!r} does not match order {order_id} "
            f"destination {order['site_id']!r}"
        )
    if order["din"] != din:
        raise ValueError(
            f"Delivery DIN {din!r} does not match order {order_id} "
            f"medication {order['din']!r}"
        )
    qty = int(order["qty"])
    if units > qty:
        raise ValueError(f"Delivery {units} exceeds remaining order qty {qty}")
    world.inventory[(site_id, din)] += units
    order["qty"] = str(qty - units)
    world.log(
        day,
        as_of,
        "delivery_received",
        order_id=order_id,
        site_id=site_id,
        din=din,
        units=units,
        remaining_order_qty=qty - units,
    )


def apply_usage_change(
    world: World,
    day: int,
    as_of: date,
    *,
    site_id: str,
    din: str,
    avg_daily_use: float,
) -> None:
    key = (site_id, din)
    previous = world.usage[key]
    world.usage[key] = float(avg_daily_use)
    world.log(
        day,
        as_of,
        "usage_change",
        site_id=site_id,
        din=din,
        previous=previous,
        avg_daily_use=avg_daily_use,
    )


def write_day_snapshot(world: World, day_dir: Path, as_of: date) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    write_csv(day_dir / "sites.csv", world.sites, ["site_id", "name"])
    write_csv(day_dir / "drugs.csv", world.drugs, ["din", "name", "criticality"])
    write_csv(
        day_dir / "substitutions.csv",
        world.substitutions,
        ["din", "substitute_din", "note", "source", "approved"],
    )

    inv_rows = [
        {
            "site_id": site,
            "din": din,
            "on_hand": on_hand,
            "as_of": as_of.isoformat(),
        }
        for (site, din), on_hand in sorted(world.inventory.items())
    ]
    usage_rows = [
        {
            "site_id": site,
            "din": din,
            "avg_daily_use": int(daily) if float(daily).is_integer() else daily,
        }
        for (site, din), daily in sorted(world.usage.items())
    ]
    write_csv(day_dir / "inventory.csv", inv_rows, ["site_id", "din", "on_hand", "as_of"])
    write_csv(day_dir / "usage.csv", usage_rows, ["site_id", "din", "avg_daily_use"])
    write_csv(
        day_dir / "open_orders.csv",
        world.orders,
        ["order_id", "din", "supplier", "site_id", "qty", "expected_date", "status"],
    )


def focus_metrics(world: World, as_of: date, delay_days: int = 0) -> dict:
    """Risk metrics for FOCUS_DIN.

    Day 1 uses agent-style cover_days_by_site (per-site days-to-due + delay).

    From Day 2 onward this report uses a SHARED disruption horizon: days until
    delayed O-1001 arrives. That is a demonstration choice for the focus-DIN
    story. It is NOT the same as giving every hospital its own delivery schedule.
    """
    stock_rows = []
    for site in (s["site_id"] for s in world.sites):
        key = (site, FOCUS_DIN)
        on_hand = world.inventory[key]
        daily = world.usage[key]
        stock_rows.append(
            {
                "site_id": site,
                "site": next(s["name"] for s in world.sites if s["site_id"] == site),
                "on_hand": on_hand,
                "daily_use": daily,
                "days_of_supply": round(on_hand / daily, 1) if daily else None,
            }
        )
    orders = [row for row in world.orders if row["din"] == FOCUS_DIN]

    if delay_days > 0:
        cover = cover_days_by_site(stock_rows, orders, delay_days=delay_days, today=as_of)
    else:
        focus_order = next(row for row in world.orders if row["order_id"] == "O-1001")
        due = date.fromisoformat(focus_order["expected_date"])
        horizon = max(1, (due - as_of).days)
        cover = {row["site_id"]: horizon for row in stock_rows}

    at_risk, shortfall = calculate_site_risk(stock_rows, cover)
    return {
        "stock": stock_rows,
        "cover": cover,
        "at_risk": at_risk,
        "shortfall": shortfall,
    }


def _looks_like_progression_days(days_root: Path) -> bool:
    """True only when days/ is empty or contains only YYYY-MM-DD snapshot dirs."""
    children = list(days_root.iterdir())
    if not children:
        return True
    for child in children:
        if not child.is_dir():
            return False
        try:
            date.fromisoformat(child.name)
        except ValueError:
            return False
    return True


def prepare_days_dir(out: Path) -> Path:
    """Create out/days, deleting a prior generated tree only when it looks safe."""
    out = out.resolve()
    if out == out.anchor or out == Path.home().resolve():
        raise ValueError(f"Refusing unsafe progression output directory: {out}")

    days_root = out / "days"
    if days_root.exists():
        if not _looks_like_progression_days(days_root):
            raise ValueError(
                f"Refusing to delete {days_root}: it does not look like a "
                "generated progression days/ directory (expected only YYYY-MM-DD folders)."
            )
        shutil.rmtree(days_root)
    days_root.mkdir(parents=True, exist_ok=True)
    return days_root


def run_progression(source: Path, out: Path) -> World:
    days_root = prepare_days_dir(out)

    world = load_world(source)
    daily_reports: list[dict] = []

    for day in range(1, NUM_DAYS + 1):
        as_of = START + timedelta(days=day - 1)

        if day == 1:
            world.log(day, as_of, "baseline", note="Phase 1 data/synthetic/mock/ morning snapshot")
        else:
            # Same-day usage changes apply BEFORE consumption.
            if day == 6:
                apply_usage_change(
                    world,
                    day,
                    as_of,
                    site_id="C",
                    din=FOCUS_DIN,
                    avg_daily_use=12,
                )
                world.log(
                    day,
                    as_of,
                    "demand_spike",
                    site_id="C",
                    din=FOCUS_DIN,
                    note="Harbour usage doubles for focus DIN (applies before today's consume)",
                )
            elif day == 10:
                apply_usage_change(
                    world,
                    day,
                    as_of,
                    site_id="C",
                    din=FOCUS_DIN,
                    avg_daily_use=6,
                )
                world.log(
                    day,
                    as_of,
                    "stabilizing",
                    note="Harbour usage returns toward baseline before today's consume",
                )

            if day == 2:
                apply_delay_order(world, day, as_of, "O-1001", "2026-12-07")
                world.log(
                    day,
                    as_of,
                    "supplier_disruption",
                    note="Meridian delay announced for cyclophosphamide 500 mg",
                    din=FOCUS_DIN,
                )

            consume_one_day(world, day, as_of)

            if day == 4:
                world.log(
                    day,
                    as_of,
                    "transfer_proposed",
                    din=FOCUS_DIN,
                    from_site="B",
                    to_site="A",
                    units=150,
                    note=(
                        "SCRIPTED simulation event (not a live Bedrock planner output). "
                        "Inventory unchanged until receipt."
                    ),
                )
            elif day == 5:
                apply_transfer_received(
                    world,
                    day,
                    as_of,
                    din=FOCUS_DIN,
                    from_site="B",
                    to_site="A",
                    units=100,
                )
            elif day == 7:
                world.log(
                    day,
                    as_of,
                    "transfer_proposed",
                    din=FOCUS_DIN,
                    from_site="D",
                    to_site="A",
                    units=40,
                    note="SCRIPTED replan event after Harbour spike — not agent-generated",
                )
                world.log(
                    day,
                    as_of,
                    "transfer_rejected",
                    din=FOCUS_DIN,
                    from_site="D",
                    to_site="A",
                    units=40,
                    note="SCRIPTED rejection — no inventory movement",
                )
            elif day == 8:
                apply_delivery(
                    world,
                    day,
                    as_of,
                    order_id="O-1001",
                    site_id="A",
                    din=FOCUS_DIN,
                    units=80,
                )
            elif day == 9:
                world.log(
                    day,
                    as_of,
                    "followup_check",
                    note=(
                        "SCRIPTED follow-up checkpoint for the demo ledger. "
                        "Not a live FollowUpAgent Bedrock decision."
                    ),
                )

        write_day_snapshot(world, days_root / as_of.isoformat(), as_of)

        # After day 2 the delay is baked into expected_date; use delay_days=0.
        delay_days = 21 if day == 1 else 0
        metrics = focus_metrics(world, as_of, delay_days=delay_days)
        daily_reports.append(
            {
                "day": day,
                "date": as_of.isoformat(),
                "delay_days_used_for_risk": delay_days,
                "at_risk": metrics["at_risk"],
                "shortfall": metrics["shortfall"],
                "sites": {
                    row["site_id"]: {
                        "on_hand": row["on_hand"],
                        "daily_use": row["daily_use"],
                        "days_of_supply": row["days_of_supply"],
                        "cover_days": metrics["cover"][row["site_id"]],
                    }
                    for row in metrics["stock"]
                    if row["site_id"] in {"A", "B", "C", "D"}
                },
            }
        )

        # Compatibility check: each snapshot loads through CsvData.
        CsvData(days_root / as_of.isoformat()).stock(FOCUS_DIN)

    (out / "ledger.json").write_text(
        json.dumps(world.ledger, indent=2), encoding="utf-8"
    )
    (out / "daily_risk.json").write_text(
        json.dumps(daily_reports, indent=2), encoding="utf-8"
    )
    write_report(out, daily_reports, world.ledger)
    return world


def write_report(out: Path, daily_reports: list[dict], ledger: list[dict]) -> None:
    lines = [
        "# Progression report — Cyclophosphamide 500 mg (`99000001`)",
        "",
        "Synthetic timeline generated from Phase 1 `data/synthetic/mock/` without modifying it.",
        "",
        f"- Start date: **{START.isoformat()}**",
        f"- Days: **{NUM_DAYS}**",
        f"- Focus DIN: **{FOCUS_DIN}**",
        "",
        "## Risk horizon note",
        "",
        "Day 1 cover days use the agent-style per-site calculation "
        "(`days until each site's own open order` + a hypothetical 21-day delay).",
        "",
        "From Day 2 onward, **Cover days** in this report are a **shared disruption "
        "horizon**: days until delayed order O-1001 arrives. This does **not** mean "
        "every hospital shares O-1001 as its own delivery. It is a demonstration "
        "lens for the focus-DIN network story.",
        "",
        "## Scripted events vs live agents",
        "",
        "Ledger events such as `transfer_proposed`, `transfer_rejected`, and "
        "`followup_check` are **scripted simulation markers**. They are not outputs "
        "from Bedrock Signal/Planner/Follow-up agents. The daily CSV folders can be "
        "loaded by those agents via `CsvData` for live demos.",
        "",
        "Same-day `usage_change` events apply **before** that day's consumption.",
        "",
        "## Day-by-day (sites A–D)",
        "",
        "| Day | Date | Site | Stock | Daily use | Days supply | Cover days (see note) | At risk? | Event notes |",
        "|---:|---|---|---:|---:|---:|---:|:---:|---|",
    ]

    events_by_day: dict[int, list[str]] = {}
    for event in ledger:
        if event["type"] == "consume":
            continue
        events_by_day.setdefault(event["day"], []).append(event["type"])

    for report in daily_reports:
        day = report["day"]
        note = ", ".join(events_by_day.get(day, ["—"]))
        for site_id, info in report["sites"].items():
            risk = "yes" if site_id in report["at_risk"] else "no"
            lines.append(
                f"| {day} | {report['date']} | {site_id} | {info['on_hand']} | "
                f"{info['daily_use']} | {info['days_of_supply']} | "
                f"{info['cover_days']} | {risk} | {note if site_id == 'A' else ''} |"
            )

    lines.extend(
        [
            "",
            "## Story arc",
            "",
            "1. **Day 1:** Baseline Phase 1 stocks; A already thin if a 3-week delay is assumed.",
            "2. **Day 2:** Supplier delay applied to O-1001 (`expected_date` → 2026-12-07); consumption continues.",
            "3. **Days 3–4:** Stock declines; transfer B→A (150) proposed (not yet moved).",
            "4. **Day 5:** Partial confirmation — only 100 units received B→A.",
            "5. **Day 6:** Harbour (C) demand spike (usage rises before that day's consume).",
            "6. **Day 7:** Scripted replan proposes D→A; scripted rejection (no stock movement).",
            "7. **Day 8:** Partial supplier delivery (+80 to A against O-1001).",
            "8. **Days 9–10:** Scripted follow-up checkpoint; Harbour usage eases before consume; A recovering.",
            "",
            "## How to inspect",
            "",
            "```powershell",
            "python -m scripts.synthetic.generate_progression",
            "python -c \"from services.agents.data.data import CsvData; "
            "print(CsvData('data/synthetic/progression/days/2026-11-19').stock('99000001'))\"",
            "```",
            "",
            "Ledger: `ledger.json`. Risk series: `daily_risk.json`.",
            "",
        ]
    )
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/synthetic/mock"))
    parser.add_argument("--out", type=Path, default=Path("data/synthetic/progression"))
    args = parser.parse_args()
    run_progression(args.source, args.out)
    print(f"wrote {args.out}/days/ ({NUM_DAYS} snapshots), ledger.json, REPORT.md")


if __name__ == "__main__":
    main()
