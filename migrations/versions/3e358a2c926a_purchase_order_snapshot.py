"""purchase order snapshot

Revision ID: 3e358a2c926a
Revises: 2ddcf23c2c6c
Create Date: 2026-11-17 03:00:00.000000

Order history, the way inventory_snapshot and usage_record already keep stock
and usage: one row per order per feed date. Each day's open_orders.csv is the
full list of open orders that day, so "orders as of day D" is the snapshot
taken on the latest feed date on or before D.

Without it, reading Nov 15 stock alongside orders already updated to Nov 24
double-counts a supplier delay (the order's ETA had already moved).

purchase_order stays the current state of every order, including ones the
platform itself creates later (order agent drafts), which have no snapshots.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "3e358a2c926a"
down_revision: Union[str, Sequence[str], None] = "2ddcf23c2c6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE purchase_order_snapshot (
            order_id TEXT NOT NULL,
            as_of DATE NOT NULL,
            din TEXT NOT NULL REFERENCES tracked_drug (din),
            site_id TEXT NOT NULL REFERENCES site (site_id),
            supplier_id TEXT REFERENCES supplier (supplier_id),
            qty INT NOT NULL CHECK (qty > 0),
            status TEXT NOT NULL
                   CHECK (status IN ('draft', 'pending_approval', 'approved', 'placed',
                                     'in_transit', 'delivered', 'delayed', 'cancelled')),
            expected_date DATE,
            source_ref TEXT NOT NULL,
            PRIMARY KEY (order_id, as_of)
        )
    """)
    op.execute("CREATE INDEX ix_purchase_order_snapshot_as_of ON purchase_order_snapshot (as_of, din)")


def downgrade() -> None:
    op.execute("DROP TABLE purchase_order_snapshot")
