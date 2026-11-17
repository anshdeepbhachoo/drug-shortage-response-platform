"""PostgresData: the agents' data source, backed by the operational tables.

Same five methods and the same return shapes as CsvData, so the agents and
their tools work unchanged:

    process_email(email, data=PostgresData.from_env())

Connect with the read-only role (readonly_app). Agents never write; our code
writes their results separately (see results_store.py).

as_of picks the stock and usage snapshot to read (latest on or before that
date), so the agents can be run against any day of the loaded progression.
Orders are not kept as history, so they are always the latest loaded.
"""

from __future__ import annotations

import os
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Orders that will actually arrive. Drafts, approved-but-unsent, delivered and
# cancelled orders don't count towards cover (CsvData can't tell them apart).
INCOMING_STATUSES = ("placed", "in_transit", "delayed")

# When usage is stored one row per day, daily_use is the average over this many days.
DAILY_AVERAGE_DAYS = 14


class PostgresData:
    def __init__(self, engine: Engine, as_of: date | None = None):
        self.engine = engine
        self.as_of = as_of

    @classmethod
    def from_env(cls, as_of: date | None = None) -> "PostgresData":
        url = os.environ.get("DATABASE_URL_READONLY") or os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL_READONLY is not set (check your .env)")
        return cls(create_engine(url), as_of)

    def _rows(self, sql: str, **params) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(text(sql), params)]

    # ---------------------------------------------------------
    # Drug catalogue
    # ---------------------------------------------------------

    def search_drugs(self, query):
        query = query.strip()
        if not query:
            return []
        return self._rows(
            """
            SELECT din, name, criticality FROM tracked_drug
            WHERE name ILIKE '%' || :query || '%'
            ORDER BY din
            """,
            query=query,
        )

    def drug(self, din):
        rows = self._rows("SELECT din, name, criticality FROM tracked_drug WHERE din = :din", din=din)
        return rows[0] if rows else None

    # ---------------------------------------------------------
    # Stock: latest snapshot per site (on or before as_of) + daily usage
    # ---------------------------------------------------------

    def stock(self, din):
        inventory = self._rows(
            """
            SELECT DISTINCT ON (i.site_id) i.site_id, s.name AS site, i.on_hand
            FROM inventory_snapshot i JOIN site s USING (site_id)
            WHERE i.din = :din AND (CAST(:as_of AS date) IS NULL OR i.as_of <= CAST(:as_of AS date))
            ORDER BY i.site_id, i.as_of DESC
            """,
            din=din,
            as_of=self.as_of,
        )
        usage = {row["site_id"]: row["daily_use"] for row in self._usage(din)}

        results = []
        for row in inventory:
            site_id = row["site_id"]
            if usage.get(site_id) is None:
                raise ValueError(f"Missing daily usage for DIN {din}, site {site_id}")

            on_hand = int(row["on_hand"])
            daily_use = float(usage[site_id])
            results.append({
                "site_id": site_id,
                "site": row["site"],
                "on_hand": on_hand,
                "daily_use": daily_use,
                "days_of_supply": round(on_hand / daily_use, 1) if daily_use > 0 else None,
            })
        return results

    def _usage(self, din):
        """Per site: the latest averaged row, or the average of recent daily rows."""

        return self._rows(
            """
            WITH latest AS (
                SELECT DISTINCT ON (site_id) site_id, usage_date, units_used, window_days
                FROM usage_record
                WHERE din = :din AND (CAST(:as_of AS date) IS NULL OR usage_date <= CAST(:as_of AS date))
                ORDER BY site_id, usage_date DESC
            )
            SELECT l.site_id,
                   CASE WHEN l.window_days > 1 THEN l.units_used
                        ELSE (SELECT avg(u.units_used) FROM usage_record u
                              WHERE u.din = :din AND u.site_id = l.site_id AND u.window_days = 1
                                AND u.usage_date <= l.usage_date
                                AND u.usage_date > l.usage_date - :days)
                   END AS daily_use
            FROM latest l
            """,
            din=din,
            as_of=self.as_of,
            days=DAILY_AVERAGE_DAYS,
        )

    # ---------------------------------------------------------
    # Orders and substitutions (string values, as CsvData returns them)
    # ---------------------------------------------------------

    def orders(self, din):
        rows = self._rows(
            """
            SELECT o.order_id, o.din, s.name AS supplier, o.site_id, o.qty,
                   o.expected_date, o.status
            FROM purchase_order o LEFT JOIN supplier s USING (supplier_id)
            WHERE o.din = :din AND o.status = ANY(CAST(:statuses AS text[]))
              AND o.expected_date IS NOT NULL
            ORDER BY o.order_id
            """,
            din=din,
            statuses=list(INCOMING_STATUSES),
        )
        return [
            {
                "order_id": row["order_id"],
                "din": row["din"],
                "supplier": row["supplier"] or "",
                "site_id": row["site_id"],
                "qty": str(row["qty"]),
                "expected_date": row["expected_date"].isoformat(),
                "status": row["status"],
            }
            for row in rows
        ]

    def substitutions(self, din):
        rows = self._rows(
            """
            SELECT din, substitute_din, note, source, approved
            FROM substitution WHERE din = :din ORDER BY substitute_din
            """,
            din=din,
        )
        return [{**row, "approved": "yes" if row["approved"] else "no"} for row in rows]
