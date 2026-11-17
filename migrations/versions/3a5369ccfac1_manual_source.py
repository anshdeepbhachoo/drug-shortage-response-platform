"""manual source

Revision ID: 3a5369ccfac1
Revises: 338e9aa46828
Create Date: 2026-11-17 06:00:00.000000

Lets a person raise a supply event themselves (the supply manager's
"Plan a response" on the Stock page), without waiting for an email or the
stock watch.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "3a5369ccfac1"
down_revision: Union[str, Sequence[str], None] = "338e9aa46828"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE supply_event DROP CONSTRAINT supply_event_source_type_check")
    op.execute("""
        ALTER TABLE supply_event ADD CONSTRAINT supply_event_source_type_check
        CHECK (source_type IN ('supplier_email', 'shortage_report', 'site_report', 'stock_watch', 'manual'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE supply_event DROP CONSTRAINT supply_event_source_type_check")
    # Keep the events (plans point at them): relabel to the closest older source.
    op.execute("UPDATE supply_event SET source_type = 'site_report' WHERE source_type = 'manual'")
    op.execute("""
        ALTER TABLE supply_event ADD CONSTRAINT supply_event_source_type_check
        CHECK (source_type IN ('supplier_email', 'shortage_report', 'site_report', 'stock_watch'))
    """)
