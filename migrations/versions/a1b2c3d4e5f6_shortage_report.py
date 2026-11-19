"""shortage report table

Revision ID: a1b2c3d4e5f6
Revises: 69b1f565ee3a
Create Date: 2026-11-11 13:50:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "69b1f565ee3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive only. Join to drug_product later via DIN (no FK required for MVP).
    op.execute("""
        CREATE TABLE shortage_report (
            report_id BIGINT PRIMARY KEY,
            din TEXT CHECK (din IS NULL OR din ~ '^[0-9]{8}$'),
            brand_name TEXT,
            company_name TEXT,
            status TEXT,
            report_type TEXT,
            updated_date TIMESTAMPTZ,
            tier3 BOOLEAN,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_shortage_report_din ON shortage_report (din)")
    op.execute("CREATE INDEX ix_shortage_report_status ON shortage_report (status)")
    op.execute("CREATE INDEX ix_shortage_report_updated ON shortage_report (updated_date)")


def downgrade() -> None:
    op.execute("DROP TABLE shortage_report")
