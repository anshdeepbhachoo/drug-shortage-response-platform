"""lineage tables

Revision ID: 26fa5d400443
Revises: 
Create Date: 2026-11-08 19:50:58.682097

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '26fa5d400443'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ingestion_run (
            run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'running'
                  CHECK (state IN ('running', 'succeeded', 'failed')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            rows_in INT NOT NULL DEFAULT 0,
            rows_ok INT NOT NULL DEFAULT 0,
            rows_quarantined INT NOT NULL DEFAULT 0,
            error TEXT,
            CHECK ((state = 'running') = (finished_at IS NULL))
        )
    """)
    op.execute("""
        CREATE TABLE raw_snapshot (
            snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id BIGINT NOT NULL REFERENCES ingestion_run (run_id),
            source TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            request_params JSONB NOT NULL,
            http_status INT,
            payload JSONB,
            payload_uri TEXT,
            payload_sha256 CHAR(64) NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (payload IS NOT NULL OR payload_uri IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE TABLE quarantine (
            quarantine_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            record JSONB NOT NULL,
            error_code TEXT NOT NULL,
            error_detail TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_raw_snapshot_run ON raw_snapshot (run_id)")
    op.execute("CREATE INDEX ix_quarantine_snapshot ON quarantine (snapshot_id)")


def downgrade() -> None:
    op.execute("DROP TABLE quarantine")
    op.execute("DROP TABLE raw_snapshot")
    op.execute("DROP TABLE ingestion_run")
