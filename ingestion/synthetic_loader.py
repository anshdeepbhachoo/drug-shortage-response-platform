"""Load the synthetic hospital CSVs into the operational tables.

Reads the CsvData layout Rayyan's generators produce (see docs/SYNTHETIC_DATA.md):
sites, drugs, inventory, usage, open_orders, substitutions.

Each CSV is kept whole in raw_snapshot, every row is validated, bad rows go to
quarantine, and good rows are upserted, so re-running is safe. Every loaded
row's source_ref is "snapshot:<id>", pointing at the raw file it came from.

Usage:
  python -m ingestion.synthetic_loader                       # mock/ (current day)
  python -m ingestion.synthetic_loader --day 2026-11-19      # one progression day
  python -m ingestion.synthetic_loader --progression         # all 10 days, oldest first

Loading the progression keeps every day's stock and usage as history (one
snapshot per day). Orders and drugs are upserted, so they end as of the last
day loaded.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ingestion.lineage import finish_run, get_engine, normalize_din, quarantine, save_snapshot, start_run

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
PROGRESSION_DAYS = ROOT / "scenarios" / "progression" / "days"
SOURCE = "synthetic_csv"
DEFAULT_USAGE_WINDOW = 14  # days each avg_daily_use value is taken to cover

CRITICALITY = {"critical", "high", "medium", "low"}
ORDER_STATUS = {
    "open": "placed",  # the synthetic data uses "open" for a placed order
    **{s: s for s in ("draft", "pending_approval", "approved", "placed",
                      "in_transit", "delivered", "delayed", "cancelled")},
}

Row = dict[str, Any]
Result = tuple[Row | None, tuple[str, str] | None]


def supplier_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _bad(code: str, detail: str) -> Result:
    return None, (code, detail)


def _int(value: Any, field: str, minimum: int = 0) -> int:
    number = int(str(value).strip())
    if number < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return number


def _float(value: Any, field: str) -> float:
    number = float(str(value).strip())
    if number < 0 or number != number:  # negative or NaN
        raise ValueError(f"{field} must be a number >= 0")
    return number


def _date(value: Any) -> str:
    return date.fromisoformat(str(value).strip()).isoformat()


# ---------------------------------------------------------
# Row validators: CSV row -> table row, or a quarantine reason
# ---------------------------------------------------------

def site_row(r: Row, _: dict) -> Result:
    if not (r.get("site_id") or "").strip() or not (r.get("name") or "").strip():
        return _bad("MISSING_FIELD", "site_id and name are required")
    return {"site_id": r["site_id"].strip(), "name": r["name"].strip()}, None


def drug_row(r: Row, _: dict) -> Result:
    din = normalize_din(r.get("din"))
    if din is None:
        return _bad("DIN_FORMAT", f"DIN {r.get('din')!r} is not 7-8 digits")
    criticality = (r.get("criticality") or "").strip().lower()
    if criticality not in CRITICALITY:
        return _bad("UNKNOWN_CRITICALITY", f"criticality {criticality!r} not in {sorted(CRITICALITY)}")
    if not (r.get("name") or "").strip():
        return _bad("MISSING_FIELD", "name is required")
    return {"din": din, "name": r["name"].strip(), "criticality": criticality, "unit": r.get("unit") or None}, None


def _site_and_drug(r: Row, known: dict) -> tuple[str | None, str | None, tuple[str, str] | None]:
    din = normalize_din(r.get("din"))
    site = (r.get("site_id") or "").strip()
    if din is None:
        return None, None, ("DIN_FORMAT", f"DIN {r.get('din')!r} is not 7-8 digits")
    if din not in known["drugs"]:
        return None, None, ("UNKNOWN_DRUG", f"DIN {din} is not in drugs.csv")
    if site not in known["sites"]:
        return None, None, ("UNKNOWN_SITE", f"site {site!r} is not in sites.csv")
    return site, din, None


def inventory_row(r: Row, known: dict) -> Result:
    site, din, problem = _site_and_drug(r, known)
    if problem:
        return None, problem
    try:
        return {"site_id": site, "din": din, "on_hand": _int(r["on_hand"], "on_hand"), "as_of": _date(r["as_of"])}, None
    except (KeyError, ValueError) as error:
        return _bad("BAD_VALUE", str(error))


def usage_row(r: Row, known: dict) -> Result:
    site, din, problem = _site_and_drug(r, known)
    if problem:
        return None, problem
    try:
        return {
            "site_id": site,
            "din": din,
            "units_used": _float(r["avg_daily_use"], "avg_daily_use"),
            # usage.csv has no date: it is the average as of the inventory snapshot.
            "usage_date": known["as_of"] or date.today().isoformat(),
            "window_days": known["usage_window"],
        }, None
    except (KeyError, ValueError) as error:
        return _bad("BAD_VALUE", str(error))


def order_row(r: Row, known: dict) -> Result:
    site, din, problem = _site_and_drug(r, known)
    if problem:
        return None, problem
    status = ORDER_STATUS.get((r.get("status") or "").strip().lower())
    if status is None:
        return _bad("UNKNOWN_STATUS", f"order status {r.get('status')!r}")
    if not (r.get("order_id") or "").strip() or not (r.get("supplier") or "").strip():
        return _bad("MISSING_FIELD", "order_id and supplier are required")
    try:
        return {
            "order_id": r["order_id"].strip(),
            "din": din,
            "site_id": site,
            "supplier_id": supplier_id(r["supplier"]),
            "supplier_name": r["supplier"].strip(),
            "qty": _int(r["qty"], "qty", minimum=1),
            "status": status,
            "expected_date": _date(r["expected_date"]),
        }, None
    except (KeyError, ValueError) as error:
        return _bad("BAD_VALUE", str(error))


def substitution_row(r: Row, known: dict) -> Result:
    din = normalize_din(r.get("din"))
    sub = normalize_din(r.get("substitute_din"))
    if din is None or sub is None:
        return _bad("DIN_FORMAT", "din and substitute_din must be 7-8 digits")
    if din not in known["drugs"]:
        return _bad("UNKNOWN_DRUG", f"DIN {din} is not in drugs.csv")
    if din == sub:
        return _bad("SELF_SUBSTITUTE", "a drug cannot substitute itself")
    if not (r.get("source") or "").strip():
        return _bad("MISSING_SOURCE", "every substitution needs a source")
    return {
        "din": din,
        "substitute_din": sub,
        "note": r.get("note"),
        "source": r["source"].strip(),
        "approved": (r.get("approved") or "").strip().lower() in {"yes", "true", "1"},
    }, None


# ---------------------------------------------------------
# Upserts
# ---------------------------------------------------------

UPSERTS = {
    "site": """
        INSERT INTO site (site_id, name) VALUES (:site_id, :name)
        ON CONFLICT (site_id) DO UPDATE SET name = EXCLUDED.name
    """,
    "tracked_drug": """
        INSERT INTO tracked_drug (din, name, criticality, unit, source_ref)
        VALUES (:din, :name, :criticality, :unit, :source_ref)
        ON CONFLICT (din) DO UPDATE SET name = EXCLUDED.name, criticality = EXCLUDED.criticality,
            unit = EXCLUDED.unit, source_ref = EXCLUDED.source_ref
    """,
    "supplier": """
        INSERT INTO supplier (supplier_id, name) VALUES (:supplier_id, :name)
        ON CONFLICT (supplier_id) DO UPDATE SET name = EXCLUDED.name
    """,
    "inventory_snapshot": """
        INSERT INTO inventory_snapshot (site_id, din, on_hand, as_of, source_ref)
        VALUES (:site_id, :din, :on_hand, :as_of, :source_ref)
        ON CONFLICT (site_id, din, as_of) DO UPDATE SET on_hand = EXCLUDED.on_hand, source_ref = EXCLUDED.source_ref
    """,
    "usage_record": """
        INSERT INTO usage_record (site_id, din, usage_date, units_used, window_days, source_ref)
        VALUES (:site_id, :din, :usage_date, :units_used, :window_days, :source_ref)
        ON CONFLICT (site_id, din, usage_date) DO UPDATE SET units_used = EXCLUDED.units_used,
            window_days = EXCLUDED.window_days, source_ref = EXCLUDED.source_ref
    """,
    "purchase_order": """
        INSERT INTO purchase_order (order_id, din, site_id, supplier_id, qty, status, expected_date, source_ref)
        VALUES (:order_id, :din, :site_id, :supplier_id, :qty, :status, :expected_date, :source_ref)
        ON CONFLICT (order_id) DO UPDATE SET din = EXCLUDED.din, site_id = EXCLUDED.site_id,
            supplier_id = EXCLUDED.supplier_id, qty = EXCLUDED.qty, status = EXCLUDED.status,
            expected_date = EXCLUDED.expected_date, source_ref = EXCLUDED.source_ref
    """,
    "substitution": """
        INSERT INTO substitution (din, substitute_din, note, source, approved)
        VALUES (:din, :substitute_din, :note, :source, :approved)
        ON CONFLICT (din, substitute_din) DO UPDATE SET note = EXCLUDED.note,
            source = EXCLUDED.source, approved = EXCLUDED.approved
    """,
}

# (file, table, validator), in foreign-key order.
FILES: list[tuple[str, str, Callable[[Row, dict], Result]]] = [
    ("sites", "site", site_row),
    ("drugs", "tracked_drug", drug_row),
    ("inventory", "inventory_snapshot", inventory_row),
    ("usage", "usage_record", usage_row),
    ("open_orders", "purchase_order", order_row),
    ("substitutions", "substitution", substitution_row),
]


def read_csv(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _known_from_db(conn: Connection, usage_window: int) -> dict:
    """Sites and drugs already loaded count as known, so a later file can refer to them."""

    return {
        "sites": {row[0] for row in conn.execute(text("SELECT site_id FROM site"))},
        "drugs": {row[0] for row in conn.execute(text("SELECT din FROM tracked_drug"))},
        "as_of": None,
        "usage_window": usage_window,
    }


def _load_file(conn: Connection, run_id: int, folder: Path, name: str, table: str, validate, known: dict, stats: dict) -> None:
    path = folder / f"{name}.csv"
    rows = read_csv(path)
    snapshot_id = save_snapshot(conn, run_id, SOURCE, path.name, {"folder": str(folder)}, rows)

    for raw in rows:
        stats["rows_in"] += 1
        row, problem = validate(raw, known)
        if problem:
            quarantine(conn, snapshot_id, raw, *problem)
            stats["rows_quarantined"] += 1
            continue

        if table == "purchase_order":
            conn.execute(text(UPSERTS["supplier"]), {"supplier_id": row["supplier_id"], "name": row["supplier_name"]})

        conn.execute(text(UPSERTS[table]), {**row, "source_ref": f"snapshot:{snapshot_id}"})
        stats["rows_ok"] += 1

        if table == "site":
            known["sites"].add(row["site_id"])
        elif table == "tracked_drug":
            known["drugs"].add(row["din"])
        elif table == "inventory_snapshot":
            known["as_of"] = max(known["as_of"] or row["as_of"], row["as_of"])


def load_folder(engine: Engine, folder: Path, usage_window: int = DEFAULT_USAGE_WINDOW) -> dict[str, int]:
    """Load one CsvData folder (mock/ or one progression day) in one transaction."""

    folder = Path(folder)
    stats = {"rows_in": 0, "rows_ok": 0, "rows_quarantined": 0}
    run_id = start_run(engine, SOURCE)

    try:
        with engine.begin() as conn:
            known = _known_from_db(conn, usage_window)
            for name, table, validate in FILES:
                _load_file(conn, run_id, folder, name, table, validate, known, stats)
        finish_run(engine, run_id, "succeeded", **stats)
    except Exception as error:
        finish_run(engine, run_id, "failed", error=f"{type(error).__name__}: {error}")
        raise

    stats["as_of"] = known["as_of"]
    return stats


def progression_folders(root: Path = PROGRESSION_DAYS) -> list[Path]:
    return sorted(path for path in Path(root).iterdir() if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name))


def load_progression(engine: Engine, root: Path = PROGRESSION_DAYS, usage_window: int = DEFAULT_USAGE_WINDOW) -> list[dict]:
    """Load every day folder, oldest first. Each day is its own ingestion_run."""

    return [{"day": folder.name, **load_folder(engine, folder, usage_window)} for folder in progression_folders(root)]


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Load synthetic hospital CSVs into the operational tables")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--folder", type=Path, help="A CsvData folder (default: mock/)")
    which.add_argument("--day", help="One progression day, e.g. 2026-11-19")
    which.add_argument("--progression", action="store_true", help="Every progression day, oldest first")
    parser.add_argument("--usage-window", type=int, default=DEFAULT_USAGE_WINDOW, help="Days each avg_daily_use covers")
    args = parser.parse_args(argv)

    try:
        engine = get_engine()
        if args.progression:
            results = load_progression(engine, usage_window=args.usage_window)
        else:
            folder = PROGRESSION_DAYS / args.day if args.day else (args.folder or MOCK)
            if not folder.is_dir():
                print(f"No such folder: {folder}", file=sys.stderr)
                return 1
            results = [{"day": folder.name, **load_folder(engine, folder, args.usage_window)}]
    except (RuntimeError, OSError) as error:
        print(f"Load failed: {error}", file=sys.stderr)
        return 1

    for result in results:
        print(f"{result['day']}: in={result['rows_in']} ok={result['rows_ok']} quarantined={result['rows_quarantined']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
