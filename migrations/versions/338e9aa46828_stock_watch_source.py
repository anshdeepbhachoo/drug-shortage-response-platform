"""stock watch source

Revision ID: 338e9aa46828
Revises: 66b076afc52d
Create Date: 2026-11-17 05:00:00.000000

Lets the daily run's stock watch raise supply events: a site in the critical
zone with no delivery arriving in time, found by scanning stock (no notice).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "338e9aa46828"
down_revision: Union[str, Sequence[str], None] = "66b076afc52d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE supply_event DROP CONSTRAINT supply_event_source_type_check")
    op.execute("""
        ALTER TABLE supply_event ADD CONSTRAINT supply_event_source_type_check
        CHECK (source_type IN ('supplier_email', 'shortage_report', 'site_report', 'stock_watch'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE supply_event DROP CONSTRAINT supply_event_source_type_check")
    # Keep the events (plans point at them): relabel to the closest older source.
    op.execute("UPDATE supply_event SET source_type = 'site_report' WHERE source_type = 'stock_watch'")
    op.execute("""
        ALTER TABLE supply_event ADD CONSTRAINT supply_event_source_type_check
        CHECK (source_type IN ('supplier_email', 'shortage_report', 'site_report'))
    """)
