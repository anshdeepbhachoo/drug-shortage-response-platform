"""orders

Revision ID: c4d5e6f7a8b9
Revises: 3a5369ccfac1
Create Date: 2026-11-17 12:00:00.000000

What the order agent (services/agents/order_agent.py, order_db.py) needs:

- supplier.on_time_rate / contact: how reliable a supplier is and where orders go.
- supplier_catalog: which supplier sells which drug, at what price, pack size,
  minimum order, lead time and availability. Synthetic (mock/supplier_catalog.csv).
- plan_action.supplier_id: an "order" option of a plan names the supplier per line
  (to_site = receiving site, units = quantity).
- purchase_order: who approved it and when, when it was placed, what the supplier
  confirmed and delivered, why it exists, and which plan action or earlier order
  it comes from. kind is NULL for orders loaded from the hospital feed and
  'routine' / 'shortage' for orders the agent drafted.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "3a5369ccfac1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE supplier
            ADD COLUMN on_time_rate NUMERIC CHECK (on_time_rate IS NULL OR on_time_rate BETWEEN 0 AND 1),
            ADD COLUMN contact TEXT
    """)

    op.execute("""
        CREATE TABLE supplier_catalog (
            supplier_id TEXT NOT NULL REFERENCES supplier (supplier_id),
            din TEXT NOT NULL REFERENCES tracked_drug (din),
            unit_cost NUMERIC NOT NULL CHECK (unit_cost >= 0),
            pack_size INT NOT NULL CHECK (pack_size > 0),
            min_order_qty INT NOT NULL CHECK (min_order_qty > 0),
            lead_time_days INT NOT NULL CHECK (lead_time_days >= 0),
            availability TEXT NOT NULL CHECK (availability IN ('in_stock', 'limited', 'backorder')),
            source_ref TEXT NOT NULL,
            PRIMARY KEY (supplier_id, din)
        )
    """)
    op.execute("CREATE INDEX ix_supplier_catalog_din ON supplier_catalog (din)")

    op.execute("ALTER TABLE plan_action ADD COLUMN supplier_id TEXT REFERENCES supplier (supplier_id)")
    op.execute("""
        ALTER TABLE plan_action ADD CONSTRAINT ck_plan_action_order
        CHECK (type <> 'order' OR (supplier_id IS NOT NULL AND to_site IS NOT NULL AND units IS NOT NULL))
    """)

    op.execute("""
        ALTER TABLE purchase_order
            ADD COLUMN kind TEXT CHECK (kind IS NULL OR kind IN ('routine', 'shortage')),
            ADD COLUMN approved_by TEXT,
            ADD COLUMN approved_role TEXT,
            ADD COLUMN placed_at TIMESTAMPTZ,
            ADD COLUMN needed_by DATE,
            ADD COLUMN confirmed_qty INT CHECK (confirmed_qty IS NULL OR confirmed_qty >= 0),
            ADD COLUMN received_qty INT CHECK (received_qty IS NULL OR received_qty >= 0),
            ADD COLUMN action_id TEXT REFERENCES plan_action (action_id),
            ADD COLUMN replaces TEXT REFERENCES purchase_order (order_id),
            ADD COLUMN reason TEXT
    """)
    # One purchase order per approved plan action.
    op.execute("CREATE UNIQUE INDEX ux_purchase_order_action ON purchase_order (action_id) WHERE action_id IS NOT NULL")
    op.execute("CREATE INDEX ix_purchase_order_kind_status ON purchase_order (kind, status) WHERE kind IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX ix_purchase_order_kind_status")
    op.execute("DROP INDEX ux_purchase_order_action")
    op.execute("""
        ALTER TABLE purchase_order
            DROP COLUMN reason, DROP COLUMN replaces, DROP COLUMN action_id, DROP COLUMN received_qty,
            DROP COLUMN confirmed_qty, DROP COLUMN needed_by, DROP COLUMN placed_at,
            DROP COLUMN approved_role, DROP COLUMN approved_by, DROP COLUMN kind
    """)
    op.execute("ALTER TABLE plan_action DROP CONSTRAINT ck_plan_action_order")
    op.execute("ALTER TABLE plan_action DROP COLUMN supplier_id")
    op.execute("DROP TABLE supplier_catalog")
    op.execute("ALTER TABLE supplier DROP COLUMN contact, DROP COLUMN on_time_rate")
