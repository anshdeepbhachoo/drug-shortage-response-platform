"""shortage report enrich columns

Revision ID: b7e8f9a0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-11 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b7e8f9a0c1d2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE shortage_report ADD COLUMN drug_code INT")
    op.execute("ALTER TABLE shortage_report ADD COLUMN ingredient TEXT")
    op.execute("ALTER TABLE shortage_report ADD COLUMN shortage_reason TEXT")
    op.execute("ALTER TABLE shortage_report ADD COLUMN actual_start_date TIMESTAMPTZ")
    op.execute("ALTER TABLE shortage_report ADD COLUMN estimated_end_date TIMESTAMPTZ")
    op.execute("CREATE INDEX ix_shortage_report_drug_code ON shortage_report (drug_code)")
    op.execute("CREATE INDEX ix_shortage_report_tier3 ON shortage_report (tier3)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_shortage_report_tier3")
    op.execute("DROP INDEX IF EXISTS ix_shortage_report_drug_code")
    op.execute("ALTER TABLE shortage_report DROP COLUMN IF EXISTS estimated_end_date")
    op.execute("ALTER TABLE shortage_report DROP COLUMN IF EXISTS actual_start_date")
    op.execute("ALTER TABLE shortage_report DROP COLUMN IF EXISTS shortage_reason")
    op.execute("ALTER TABLE shortage_report DROP COLUMN IF EXISTS ingredient")
    op.execute("ALTER TABLE shortage_report DROP COLUMN IF EXISTS drug_code")
