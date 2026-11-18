"""PostgresData: the agents' data source, backed by the operational tables.

Same five methods and the same return shapes as CsvData, so the agents and
their tools work unchanged:

    process_email(email, data=PostgresData.from_env())

Connect with the read-only role (readonly_app). Agents never write; our code
writes their results separately (see results_store.py).

as_of picks the snapshot to read (latest on or before that date) for stock,
usage and orders, so the agents can be run against any day of the loaded
progression. Without as_of, orders come from purchase_order (current state).
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
        if self.as_of is None:
            source, day_filter = "purchase_order o", "TRUE"
        else:
            # Each feed date holds the full order list for that day, so take the
            # whole list from the latest feed date on or before as_of.
            source = "purchase_order_snapshot o"
            day_filter = """o.as_of = (SELECT max(as_of) FROM purchase_order_snapshot
                                       WHERE as_of <= CAST(:as_of AS date))"""
        rows = self._rows(
            f"""
            SELECT o.order_id, o.din, s.name AS supplier, o.site_id, o.qty,
                   o.expected_date, o.status
            FROM {source} LEFT JOIN supplier s USING (supplier_id)
            WHERE o.din = :din AND o.status = ANY(CAST(:statuses AS text[]))
              AND o.expected_date IS NOT NULL
              AND {day_filter}
            ORDER BY o.order_id
            """,
            din=din,
            statuses=list(INCOMING_STATUSES),
            as_of=self.as_of,
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

    def suppliers(self, din):
        """Who has supplied this drug, and to which site (active suppliers only). With as_of, only
        orders seen on or before that day count."""

        source = "purchase_order o" if self.as_of is None else "purchase_order_snapshot o"
        day_filter = "TRUE" if self.as_of is None else "o.as_of <= CAST(:as_of AS date)"
        return self._rows(
            f"""
            SELECT DISTINCT o.supplier_id, s.name AS supplier, o.site_id
            FROM {source} JOIN supplier s USING (supplier_id)
            WHERE o.din = :din AND s.active AND {day_filter}
            ORDER BY o.supplier_id, o.site_id
            """,
            din=din,
            as_of=self.as_of,
        )

    # ---------------------------------------------------------
    # Health Canada reference data (agent tools get_shortage_reports and
    # get_drug_product). Dates come back as ISO strings so tool results are JSON.
    # ---------------------------------------------------------

    def shortage_reports(self, din, limit: int = 10):
        rows = self._rows(
            """
            SELECT report_id, status, report_type, tier3, shortage_reason, brand_name,
                   company_name, actual_start_date, estimated_end_date, updated_date
            FROM shortage_report WHERE din = :din
            ORDER BY updated_date DESC NULLS LAST, report_id DESC
            LIMIT :limit
            """,
            din=din,
            limit=limit,
        )
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in rows]

    def drug_product(self, din):
        products = self._rows(
            """
            SELECT p.drug_code, p.din, p.brand_name, p.company_name, p.class_name,
                   p.last_update_date, s.status_name AS current_status
            FROM drug_product p
            LEFT JOIN product_status ps ON ps.drug_code = p.drug_code AND ps.is_current
            LEFT JOIN ref_dpd_status s ON s.status_code = ps.status_code
            WHERE p.din = :din
            """,
            din=din,
        )
        if not products:
            return None
        product = products[0]
        code = product["drug_code"]
        product["last_update_date"] = product["last_update_date"].isoformat() if product["last_update_date"] else None
        product["ingredients"] = [
            {**row, "strength_num": float(row["strength_num"]) if row["strength_num"] is not None else None}
            for row in self._rows(
                "SELECT ingredient_name, strength, strength_unit, strength_num FROM active_ingredient WHERE drug_code = :c ORDER BY ingredient_name",
                c=code,
            )
        ]
        product["forms"] = [r["form_name"] for r in self._rows("SELECT form_name FROM dosage_form WHERE drug_code = :c ORDER BY form_name", c=code)]
        product["routes"] = [r["route_name"] for r in self._rows("SELECT route_name FROM route WHERE drug_code = :c ORDER BY route_name", c=code)]
        return product

    def substitutions(self, din):
        rows = self._rows(
            """
            SELECT din, substitute_din, note, source, approved
            FROM substitution WHERE din = :din ORDER BY substitute_din
            """,
            din=din,
        )
        return [{**row, "approved": "yes" if row["approved"] else "no"} for row in rows]
