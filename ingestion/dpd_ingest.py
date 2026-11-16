"""Load Health Canada DPD records into drug_product and its child tables.

Two phases, matching the enrichment_queue table in the DPD migration:

1. Products: for each DIN, GET /drugproduct, keep the raw response, validate,
   upsert drug_product, and queue its four detail endpoints.
2. Enrichment: work through enrichment_queue (activeingredient, status, form,
   route). Each item is its own transaction; a failure is retried later with
   backoff and marked failed after MAX_ATTEMPTS, without undoing other items.

Bad records go to quarantine with a code (DIN_FORMAT, MISSING_BRAND,
STRENGTH_NEGATIVE, UNKNOWN_STATUS, ...), never silently dropped.

Usage:
  python -m ingestion.dpd_ingest --din 02242974 --din 00326925
  python -m ingestion.dpd_ingest --din-file dins.txt     # one DIN per line
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ingestion.dpd_client import DpdAPIError, DpdClient
from ingestion.lineage import (
    finish_run,
    get_engine,
    normalize_din,
    quarantine,
    save_snapshot,
    sha256_json,
    start_run,
)

SOURCE = "dpd"
CHILD_ENDPOINTS = ("activeingredient", "status", "form", "route")
MAX_ATTEMPTS = 5

Record = dict[str, Any]


# ---------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------

def parse_decimal(value: Any) -> Decimal | None:
    """'.5' -> 0.5, ' 20 ' -> 20, '' -> None. Rejects NaN and infinity."""

    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        number = Decimal(text_value)
    except InvalidOperation as error:
        raise ValueError(f"not a number: {value!r}") from error
    if not number.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return number


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text_value = str(value).strip()
    try:
        return date.fromisoformat(text_value[:10])
    except ValueError:
        return datetime.strptime(text_value, "%d-%b-%Y").date()


def _int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(str(value).strip())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


# ---------------------------------------------------------
# Validators: API record -> table row, or (code, detail)
# ---------------------------------------------------------

def validate_product(record: Record) -> tuple[Record | None, tuple[str, str] | None]:
    try:
        drug_code = _int(record.get("drug_code"))
    except ValueError:
        drug_code = None
    if drug_code is None:
        return None, ("BAD_DRUG_CODE", f"drug_code {record.get('drug_code')!r} is not an integer")

    raw_din = record.get("drug_identification_number")
    din = normalize_din(raw_din)
    if raw_din not in (None, "") and din is None:
        return None, ("DIN_FORMAT", f"DIN {raw_din!r} is not 7-8 digits")

    brand = _text(record.get("brand_name"))
    if brand is None:
        return None, ("MISSING_BRAND", "brand_name is empty")

    try:
        number_of_ais = _int(record.get("number_of_ais"))
        last_update = parse_date(record.get("last_update_date"))
    except ValueError as error:
        return None, ("BAD_VALUE", str(error))

    return {
        "drug_code": drug_code,
        "din": din,
        "brand_name": brand,
        "company_name": _text(record.get("company_name")),
        "class_name": _text(record.get("class_name")),
        "descriptor": _text(record.get("descriptor")),
        "number_of_ais": number_of_ais,
        "ai_group_no": _text(record.get("ai_group_no")),
        "last_update_date": last_update,
    }, None


def validate_ingredient(record: Record, drug_code: int) -> tuple[Record | None, tuple[str, str] | None]:
    name = _text(record.get("ingredient_name"))
    if name is None:
        return None, ("MISSING_INGREDIENT", "ingredient_name is empty")
    try:
        strength_num = parse_decimal(record.get("strength"))
        dosage_value_num = parse_decimal(record.get("dosage_value"))
    except ValueError as error:
        return None, ("BAD_VALUE", str(error))
    if strength_num is not None and strength_num < 0:
        return None, ("STRENGTH_NEGATIVE", f"strength_num = {strength_num} for ingredient {name!r}")
    if dosage_value_num is not None and dosage_value_num <= 0:
        return None, ("DOSAGE_NOT_POSITIVE", f"dosage_value = {dosage_value_num} for ingredient {name!r}")

    # "HEPARIN SODIUM (PORCINE)" -> base "HEPARIN SODIUM", qualifier "PORCINE"
    base, _, rest = name.partition("(")
    qualifier = rest.rstrip(")").strip() or None
    strength_unit = _text(record.get("strength_unit"))
    dosage_unit = _text(record.get("dosage_unit"))
    conc = strength_num / dosage_value_num if strength_num is not None and dosage_value_num else None

    row = {
        "drug_code": drug_code,
        "ingredient_name": name,
        "ingredient_base": base.strip().upper(),
        "ingredient_qualifier": qualifier,
        "strength": _text(record.get("strength")),
        "strength_unit": strength_unit,
        "strength_num": strength_num,
        "dosage_value": _text(record.get("dosage_value")),
        "dosage_unit": dosage_unit,
        "dosage_value_num": dosage_value_num,
        "conc_per_unit": conc,
        "conc_unit": f"{strength_unit}/{dosage_unit}" if conc is not None and strength_unit and dosage_unit else None,
    }
    row["row_hash"] = sha256_json({k: row[k] for k in ("ingredient_name", "strength", "strength_unit", "dosage_value", "dosage_unit")})
    return row, None


def validate_status(record: Record, drug_code: int, known_codes: set[int]) -> tuple[Record | None, tuple[str, str] | None]:
    try:
        code = _int(record.get("external_status_code"))
    except ValueError:
        code = None
    if code is None or code not in known_codes:
        return None, ("UNKNOWN_STATUS", f"status_code {record.get('external_status_code')!r} not in ref_dpd_status")
    return {"drug_code": drug_code, "status_code": code}, None


def validate_coded(record: Record, drug_code: int, code_field: str, name_field: str) -> tuple[Record | None, tuple[str, str] | None]:
    try:
        code = _int(record.get(code_field))
    except ValueError:
        code = None
    name = _text(record.get(name_field))
    if code is None or name is None:
        return None, ("MISSING_FIELD", f"{code_field} and {name_field} are required")
    return {"drug_code": drug_code, "code": code, "name": name}, None


# ---------------------------------------------------------
# Writers
# ---------------------------------------------------------

UPSERT_PRODUCT = """
    INSERT INTO drug_product (drug_code, din, brand_name, company_name, class_name, descriptor,
                              number_of_ais, ai_group_no, last_update_date, snapshot_id)
    VALUES (:drug_code, :din, :brand_name, :company_name, :class_name, :descriptor,
            :number_of_ais, :ai_group_no, :last_update_date, :snapshot_id)
    ON CONFLICT (drug_code) DO UPDATE SET
        din = EXCLUDED.din, brand_name = EXCLUDED.brand_name, company_name = EXCLUDED.company_name,
        class_name = EXCLUDED.class_name, descriptor = EXCLUDED.descriptor,
        number_of_ais = EXCLUDED.number_of_ais, ai_group_no = EXCLUDED.ai_group_no,
        last_update_date = EXCLUDED.last_update_date, snapshot_id = EXCLUDED.snapshot_id,
        last_seen_at = now()
"""

ENQUEUE = """
    INSERT INTO enrichment_queue (drug_code, endpoint) VALUES (:drug_code, :endpoint)
    ON CONFLICT (drug_code, endpoint) DO UPDATE SET
        state = 'pending', attempts = 0, next_attempt_at = now(), last_error = NULL, updated_at = now()
"""


def _write_children(conn: Connection, endpoint: str, drug_code: int, records: list[Record], snapshot_id: int, known_codes: set[int]) -> tuple[int, int]:
    ok = bad = 0

    for record in records:
        if endpoint == "activeingredient":
            row, problem = validate_ingredient(record, drug_code)
        elif endpoint == "status":
            row, problem = validate_status(record, drug_code, known_codes)
        elif endpoint == "form":
            row, problem = validate_coded(record, drug_code, "pharmaceutical_form_code", "pharmaceutical_form_name")
        else:
            row, problem = validate_coded(record, drug_code, "route_of_administration_code", "route_of_administration_name")

        if problem:
            quarantine(conn, snapshot_id, record, *problem)
            bad += 1
            continue

        if endpoint == "activeingredient":
            conn.execute(text("""
                INSERT INTO active_ingredient (drug_code, ingredient_name, ingredient_base, ingredient_qualifier,
                    strength, strength_unit, strength_num, dosage_value, dosage_unit, dosage_value_num,
                    conc_per_unit, conc_unit, row_hash, snapshot_id)
                VALUES (:drug_code, :ingredient_name, :ingredient_base, :ingredient_qualifier,
                    :strength, :strength_unit, :strength_num, :dosage_value, :dosage_unit, :dosage_value_num,
                    :conc_per_unit, :conc_unit, :row_hash, :snapshot_id)
                ON CONFLICT (drug_code, row_hash) DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id
            """), {**row, "snapshot_id": snapshot_id})
        elif endpoint == "status":
            # The status endpoint returns the current status; older ones stop being current.
            conn.execute(text("UPDATE product_status SET is_current = FALSE WHERE drug_code = :drug_code AND status_code <> :status_code"), row)
            conn.execute(text("""
                INSERT INTO product_status (drug_code, status_code, is_current, snapshot_id)
                VALUES (:drug_code, :status_code, TRUE, :snapshot_id)
                ON CONFLICT (drug_code, status_code) DO UPDATE SET
                    is_current = TRUE, snapshot_id = EXCLUDED.snapshot_id, last_seen_at = now()
            """), {**row, "snapshot_id": snapshot_id})
        elif endpoint == "form":
            conn.execute(text("""
                INSERT INTO dosage_form (drug_code, form_code, form_name, snapshot_id)
                VALUES (:drug_code, :code, :name, :snapshot_id)
                ON CONFLICT (drug_code, form_code) DO UPDATE SET form_name = EXCLUDED.form_name, snapshot_id = EXCLUDED.snapshot_id
            """), {**row, "snapshot_id": snapshot_id})
        else:
            conn.execute(text("""
                INSERT INTO route (drug_code, route_code, route_name, snapshot_id)
                VALUES (:drug_code, :code, :name, :snapshot_id)
                ON CONFLICT (drug_code, route_code) DO UPDATE SET route_name = EXCLUDED.route_name, snapshot_id = EXCLUDED.snapshot_id
            """), {**row, "snapshot_id": snapshot_id})
        ok += 1

    return ok, bad


# ---------------------------------------------------------
# Phases
# ---------------------------------------------------------

def load_products(engine: Engine, client: DpdClient, run_id: int, dins: list[str], stats: dict) -> list[int]:
    drug_codes = []
    for din in dins:
        records = client.get("drugproduct", din=din)
        with engine.begin() as conn:
            snapshot_id = save_snapshot(conn, run_id, SOURCE, "/drugproduct", {"din": din}, records)
            for record in records:
                stats["rows_in"] += 1
                row, problem = validate_product(record)
                if problem:
                    quarantine(conn, snapshot_id, record, *problem)
                    stats["rows_quarantined"] += 1
                    continue
                conn.execute(text(UPSERT_PRODUCT), {**row, "snapshot_id": snapshot_id})
                for endpoint in CHILD_ENDPOINTS:
                    conn.execute(text(ENQUEUE), {"drug_code": row["drug_code"], "endpoint": endpoint})
                drug_codes.append(row["drug_code"])
                stats["rows_ok"] += 1
    return drug_codes


def process_queue(engine: Engine, client: DpdClient, run_id: int, stats: dict, limit: int | None = None) -> None:
    with engine.connect() as conn:
        known_codes = {row[0] for row in conn.execute(text("SELECT status_code FROM ref_dpd_status"))}
        items = conn.execute(text("""
            SELECT drug_code, endpoint FROM enrichment_queue
            WHERE state = 'pending' AND next_attempt_at <= now()
            ORDER BY drug_code, endpoint
        """ + (f" LIMIT {int(limit)}" if limit else ""))).all()

    for drug_code, endpoint in items:
        try:
            records = client.get(endpoint, id=drug_code)
            with engine.begin() as conn:
                snapshot_id = save_snapshot(conn, run_id, SOURCE, f"/{endpoint}", {"id": drug_code}, records)
                ok, bad = _write_children(conn, endpoint, drug_code, records, snapshot_id, known_codes)
                conn.execute(text("""
                    UPDATE enrichment_queue SET state = 'done', last_error = NULL, updated_at = now()
                    WHERE drug_code = :drug_code AND endpoint = :endpoint
                """), {"drug_code": drug_code, "endpoint": endpoint})
            stats["rows_in"] += ok + bad
            stats["rows_ok"] += ok
            stats["rows_quarantined"] += bad
        except (DpdAPIError, ValueError) as error:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE enrichment_queue
                    SET attempts = attempts + 1,
                        state = CASE WHEN attempts + 1 >= :max_attempts THEN 'failed' ELSE 'pending' END,
                        next_attempt_at = now() + make_interval(mins => power(2, attempts + 1)::int),
                        last_error = :error, updated_at = now()
                    WHERE drug_code = :drug_code AND endpoint = :endpoint
                """), {"drug_code": drug_code, "endpoint": endpoint, "error": str(error)[:2000], "max_attempts": MAX_ATTEMPTS})
            stats["queue_failures"] += 1


def run_ingest(engine: Engine, client: DpdClient, dins: list[str]) -> dict[str, int]:
    """Load products for the given DINs, then enrich them (and any pending queue items)."""

    stats = {"rows_in": 0, "rows_ok": 0, "rows_quarantined": 0, "queue_failures": 0}
    run_id = start_run(engine, SOURCE)
    try:
        load_products(engine, client, run_id, dins, stats)
        process_queue(engine, client, run_id, stats)
    except Exception as error:
        finish_run(engine, run_id, "failed", stats["rows_in"], stats["rows_ok"], stats["rows_quarantined"],
                   error=f"{type(error).__name__}: {error}")
        raise

    finish_run(engine, run_id, "succeeded", stats["rows_in"], stats["rows_ok"], stats["rows_quarantined"],
               error=f"{stats['queue_failures']} enrichment item(s) will be retried" if stats["queue_failures"] else None)
    return stats


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Load Health Canada DPD products for our drugs")
    parser.add_argument("--din", action="append", default=[], help="DIN to load (repeatable)")
    parser.add_argument("--din-file", type=Path, help="File with one DIN per line")
    args = parser.parse_args(argv)

    raw = list(args.din)
    if args.din_file:
        raw += [line.strip() for line in args.din_file.read_text().splitlines() if line.strip()]
    if not raw:
        parser.error("give at least one --din or a --din-file")

    dins = [normalize_din(d) for d in raw]
    if None in dins:
        bad = [d for d, n in zip(raw, dins) if n is None]
        print(f"Not 7-8 digit DINs: {bad}", file=sys.stderr)
        return 1

    try:
        with DpdClient() as client:
            stats = run_ingest(get_engine(), client, dins)
    except (DpdAPIError, RuntimeError) as error:
        print(f"DPD ingest failed: {error}", file=sys.stderr)
        return 1

    print(
        f"DPD ingest succeeded: in={stats['rows_in']} ok={stats['rows_ok']} "
        f"quarantined={stats['rows_quarantined']} retry_later={stats['queue_failures']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
