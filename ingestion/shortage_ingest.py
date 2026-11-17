"""Ingest Health Canada shortage reports into Postgres.

Reuses existing lineage tables (ingestion_run, raw_snapshot, quarantine)
and upserts into shortage_report.

Usage:
  python -m ingestion.shortage_ingest --max-pages 1
  python -m ingestion.shortage_ingest --refresh-status --max-pages 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ingestion.shortage_client import ShortageAPIError, ShortageClient

SOURCE = "health_canada_shortage"
# Accept only plain 7–8 digit values (pad 7 → 8). Do not strip letters into fake DINs.
DIN_RE = re.compile(r"^[0-9]{7,8}$")

UPSERT_SHORTAGE_SQL = """
    INSERT INTO shortage_report (
        report_id, din, drug_code, brand_name, company_name,
        ingredient, status, report_type, shortage_reason,
        updated_date, actual_start_date, estimated_end_date,
        tier3, snapshot_id
    )
    VALUES (
        :report_id, :din, :drug_code, :brand_name, :company_name,
        :ingredient, :status, :report_type, :shortage_reason,
        :updated_date, :actual_start_date, :estimated_end_date,
        :tier3, :snapshot_id
    )
    ON CONFLICT (report_id) DO UPDATE SET
        din = EXCLUDED.din,
        drug_code = EXCLUDED.drug_code,
        brand_name = EXCLUDED.brand_name,
        company_name = EXCLUDED.company_name,
        ingredient = EXCLUDED.ingredient,
        status = EXCLUDED.status,
        report_type = EXCLUDED.report_type,
        shortage_reason = EXCLUDED.shortage_reason,
        updated_date = EXCLUDED.updated_date,
        actual_start_date = EXCLUDED.actual_start_date,
        estimated_end_date = EXCLUDED.estimated_end_date,
        tier3 = EXCLUDED.tier3,
        snapshot_id = EXCLUDED.snapshot_id,
        last_seen_at = now()
    WHERE shortage_report.updated_date IS NULL
       OR (
            EXCLUDED.updated_date IS NOT NULL
            AND EXCLUDED.updated_date >= shortage_report.updated_date
       )
"""


def _sha256_json(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _normalize_din(value: Any) -> str | None:
    """Normalize DIN without inventing digits from malformed strings."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if not DIN_RE.match(raw):
        return None
    return raw.zfill(8)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text_value = str(value).strip()
    if not text_value or text_value.startswith("0001-01-01"):
        return None
    # Accept common API forms: 2019-04-11T00:00:00.000Z or without Z.
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_newer_or_equal(
    incoming: datetime | None,
    existing: datetime | None,
) -> bool:
    """Return True when incoming data should replace an existing row."""
    if existing is None:
        return True
    if incoming is None:
        return False
    return incoming >= existing


def _tier3_flag(record: dict[str, Any]) -> bool | None:
    """Tier 3 flag from the live API field `tier_3` (with a few fallbacks)."""
    for key in ("tier_3", "tier3", "is_tier3", "tier3_shortage"):
        if key in record:
            val = record[key]
            if isinstance(val, bool):
                return val
            if isinstance(val, (int, float)):
                return bool(val)
            if isinstance(val, str):
                return val.strip().lower() in {"1", "true", "yes", "y", "t3", "!!t3", "!t3"}
    return None


def _nested_label(value: Any, *keys: str) -> str | None:
    """Pull a string from a plain value or a nested object (e.g. type.label)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if nested not in (None, ""):
                return str(nested)
    return None


def _brand_name(record: dict[str, Any]) -> str | None:
    drug = record.get("drug") if isinstance(record.get("drug"), dict) else {}
    for value in (
        record.get("brand_name"),
        record.get("en_drug_brand_name"),
        drug.get("brand_name") if isinstance(drug, dict) else None,
    ):
        if value not in (None, ""):
            return str(value)
    return None


def _shortage_reason(record: dict[str, Any]) -> str | None:
    reason = record.get("shortage_reason")
    return _nested_label(reason, "en_reason", "reason", "label")


def _drug_code(record: dict[str, Any]) -> int | None:
    drug = record.get("drug")
    raw = None
    if isinstance(drug, dict):
        raw = drug.get("drug_code")
    if raw is None:
        raw = record.get("drug_code")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def validate_record(record: Any) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (normalized_row, error_code, error_detail)."""
    if not isinstance(record, dict):
        return None, "not_object", "Record is not a JSON object"

    report_id = record.get("id", record.get("report_id"))
    try:
        report_id_int = int(report_id)
    except (TypeError, ValueError):
        return None, "missing_report_id", "Report id missing or not an integer"

    raw_din = record.get("din")
    din = _normalize_din(raw_din)
    if raw_din not in (None, "") and din is None:
        return None, "invalid_din", f"Could not normalize DIN: {raw_din!r}"

    report_type = _nested_label(record.get("type") or record.get("report_type"), "label")

    return {
        "report_id": report_id_int,
        "din": din,
        "drug_code": _drug_code(record),
        "brand_name": _brand_name(record),
        "company_name": record.get("company_name"),
        "ingredient": record.get("en_ingredients"),
        "status": record.get("status"),
        "report_type": report_type,
        "shortage_reason": _shortage_reason(record),
        "updated_date": _parse_timestamp(record.get("updated_date")),
        "actual_start_date": _parse_timestamp(record.get("actual_start_date")),
        "estimated_end_date": _parse_timestamp(record.get("estimated_end_date")),
        "tier3": _tier3_flag(record),
    }, None, None


def _get_engine() -> Engine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (check your .env)")
    return create_engine(url)


def _start_run(engine: Engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO ingestion_run (source, state)
                VALUES (:source, 'running')
                RETURNING run_id
                """
            ),
            {"source": SOURCE},
        ).scalar_one()


def _finish_run(
    engine: Engine,
    *,
    run_id: int,
    state: str,
    rows_in: int,
    rows_ok: int,
    rows_quarantined: int,
    error: str | None = None,
) -> None:
    """Persist terminal run state in its own transaction (survives rollbacks)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ingestion_run
                SET state = :state,
                    finished_at = now(),
                    rows_in = :rows_in,
                    rows_ok = :rows_ok,
                    rows_quarantined = :rows_quarantined,
                    error = :error
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "state": state,
                "rows_in": rows_in,
                "rows_ok": rows_ok,
                "rows_quarantined": rows_quarantined,
                "error": error,
            },
        )


def run_ingest(
    *,
    engine: Engine,
    client: ShortageClient,
    limit: int = 100,
    max_pages: int | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, int]:
    filters = filters or {}
    rows_in = rows_ok = rows_quarantined = 0
    run_id = _start_run(engine)

    try:
        with engine.begin() as conn:
            for request_params, page in client.iter_search(
                limit=limit, max_pages=max_pages, **filters
            ):
                payload_hash = _sha256_json(page)
                snapshot_id = conn.execute(
                    text(
                        """
                        INSERT INTO raw_snapshot (
                            run_id, source, endpoint, request_params,
                            http_status, payload, payload_sha256
                        )
                        VALUES (
                            :run_id, :source, :endpoint, CAST(:params AS jsonb),
                            200, CAST(:payload AS jsonb), :sha
                        )
                        RETURNING snapshot_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "source": SOURCE,
                        "endpoint": "/search",
                        "params": json.dumps(request_params, default=str),
                        "payload": json.dumps(page, default=str),
                        "sha": payload_hash,
                    },
                ).scalar_one()

                for record in page.get("data") or []:
                    rows_in += 1
                    normalized, err_code, err_detail = validate_record(record)
                    if normalized is None:
                        rows_quarantined += 1
                        conn.execute(
                            text(
                                """
                                INSERT INTO quarantine (
                                    snapshot_id, record, error_code, error_detail
                                )
                                VALUES (
                                    :snapshot_id, CAST(:record AS jsonb),
                                    :error_code, :error_detail
                                )
                                """
                            ),
                            {
                                "snapshot_id": snapshot_id,
                                "record": json.dumps(record, default=str),
                                "error_code": err_code,
                                "error_detail": err_detail,
                            },
                        )
                        continue

                    conn.execute(
                        text(UPSERT_SHORTAGE_SQL),
                        {**normalized, "snapshot_id": snapshot_id},
                    )
                    rows_ok += 1

        _finish_run(
            engine,
            run_id=run_id,
            state="succeeded",
            rows_in=rows_in,
            rows_ok=rows_ok,
            rows_quarantined=rows_quarantined,
        )
    except Exception as exc:
        # Main write transaction rolled back, so committed counts are 0.
        # Keep attempted counts only in the error text for audit clarity.
        attempted = (
            f"attempted rows_in={rows_in} rows_ok={rows_ok} "
            f"rows_quarantined={rows_quarantined} (not committed); "
        )
        _finish_run(
            engine,
            run_id=run_id,
            state="failed",
            rows_in=0,
            rows_ok=0,
            rows_quarantined=0,
            error=(attempted + str(exc))[:2000],
        )
        raise

    return {
        "rows_in": rows_in,
        "rows_ok": rows_ok,
        "rows_quarantined": rows_quarantined,
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ingest Health Canada shortage reports")
    parser.add_argument("--limit", type=int, default=100, help="Page size")
    parser.add_argument("--max-pages", type=int, default=None, help="Stop after N pages")
    parser.add_argument("--type", dest="report_type", default="shortage", help="Report type filter")
    parser.add_argument(
        "--filter-status",
        default="active_confirmed",
        help="API filter_status (default: active_confirmed). Use '' for all.",
    )
    parser.add_argument(
        "--refresh-status",
        action="store_true",
        help=(
            "Ignore filter_status so previously active reports can update to "
            "resolved/avoided/etc. MVP caveat: with --max-pages this only "
            "refreshes reports that appear in those pages, not every known row. "
            "Use periodically; omit --max-pages for a broader refresh."
        ),
    )
    parser.add_argument("--term", default=None, help="Optional text search")
    parser.add_argument("--din", default=None, help="Optional DIN filter")
    args = parser.parse_args(argv)

    filter_status = None if args.refresh_status else (args.filter_status or None)
    filters = {
        "type": args.report_type,
        "filter_status": filter_status,
        "term": args.term,
        "din": args.din,
        "orderby": "updated_date",
        "order": "desc",
    }

    engine = _get_engine()
    try:
        with ShortageClient() as client:
            stats = run_ingest(
                engine=engine,
                client=client,
                limit=args.limit,
                max_pages=args.max_pages,
                filters=filters,
            )
    except (ShortageAPIError, RuntimeError) as exc:
        print(f"Ingest failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Ingest succeeded: in={stats['rows_in']} "
        f"ok={stats['rows_ok']} quarantined={stats['rows_quarantined']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
