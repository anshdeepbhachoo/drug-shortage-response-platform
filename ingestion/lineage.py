"""Lineage helpers shared by loaders: run tracking, raw snapshots, quarantine.

Same approach as shortage_ingest.py: one ingestion_run row per run (state
written in its own transaction so failures are recorded), the raw input kept
in raw_snapshot with its SHA-256, and rejected rows kept in quarantine.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

DIN_RE = re.compile(r"^[0-9]{7,8}$")


def get_engine(env_var: str = "DATABASE_URL") -> Engine:
    url = os.environ.get(env_var)
    if not url:
        raise RuntimeError(f"{env_var} is not set (check your .env)")
    return create_engine(url)


def normalize_din(value: Any) -> str | None:
    """8-digit DIN, padding a 7-digit one. None if missing or malformed."""

    if value is None:
        return None
    text_value = str(value).strip()
    if not DIN_RE.match(text_value):
        return None
    return text_value.zfill(8)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def start_run(engine: Engine, source: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO ingestion_run (source, state) VALUES (:source, 'running') RETURNING run_id"),
            {"source": source},
        ).scalar_one()


def finish_run(
    engine: Engine,
    run_id: int,
    state: str,
    rows_in: int = 0,
    rows_ok: int = 0,
    rows_quarantined: int = 0,
    error: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ingestion_run
                SET state = :state, finished_at = now(), rows_in = :rows_in,
                    rows_ok = :rows_ok, rows_quarantined = :rows_quarantined, error = :error
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "state": state,
                "rows_in": rows_in,
                "rows_ok": rows_ok,
                "rows_quarantined": rows_quarantined,
                "error": error[:2000] if error else None,
            },
        )


def save_snapshot(
    conn: Connection,
    run_id: int,
    source: str,
    endpoint: str,
    params: dict,
    payload: Any,
    http_status: int | None = 200,
) -> int:
    return conn.execute(
        text(
            """
            INSERT INTO raw_snapshot (run_id, source, endpoint, request_params,
                                      http_status, payload, payload_sha256)
            VALUES (:run_id, :source, :endpoint, CAST(:params AS jsonb),
                    :http_status, CAST(:payload AS jsonb), :sha)
            RETURNING snapshot_id
            """
        ),
        {
            "run_id": run_id,
            "source": source,
            "endpoint": endpoint,
            "params": json.dumps(params, default=str),
            "http_status": http_status,
            "payload": json.dumps(payload, default=str),
            "sha": sha256_json(payload),
        },
    ).scalar_one()


def quarantine(conn: Connection, snapshot_id: int, record: Any, code: str, detail: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO quarantine (snapshot_id, record, error_code, error_detail)
            VALUES (:snapshot_id, CAST(:record AS jsonb), :code, :detail)
            """
        ),
        {"snapshot_id": snapshot_id, "record": json.dumps(record, default=str), "code": code, "detail": detail},
    )
