"""order options

Revision ID: 9d4f6b2e8a13
Revises: 7c2e91d4b5a0
Create Date: 2026-11-18 10:00:00.000000

What order options in plans need (services/agents/orders/options.py):

- plan_action.supplier_id and plan_action.expected_date: an order line is one
  site's order (to_site, units) from one supplier, expected on a date worked
  out in code. A verified order line must name all four (an order option that
  failed its checks is kept as proposed, possibly incomplete).

Approving an order option writes one purchase_order row per line with status
'approved' (and plan_id, event_id): authorised, not placed. A person places it.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "9d4f6b2e8a13"
down_revision: Union[str, Sequence[str], None] = "7c2e91d4b5a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE plan_action ADD COLUMN supplier_id TEXT REFERENCES supplier (supplier_id)")
    op.execute("ALTER TABLE plan_action ADD COLUMN expected_date DATE")
    op.execute("""
        ALTER TABLE plan_action ADD CONSTRAINT ck_plan_action_order_line
        CHECK (type <> 'order' OR NOT verified OR (
            to_site IS NOT NULL AND units IS NOT NULL AND supplier_id IS NOT NULL AND expected_date IS NOT NULL))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE plan_action DROP CONSTRAINT ck_plan_action_order_line")
    op.execute("ALTER TABLE plan_action DROP COLUMN expected_date")
    op.execute("ALTER TABLE plan_action DROP COLUMN supplier_id")
