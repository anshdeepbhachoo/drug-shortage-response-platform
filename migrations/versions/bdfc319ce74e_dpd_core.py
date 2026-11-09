"""dpd core

Revision ID: bdfc319ce74e
Revises: 26fa5d400443
Create Date: 2026-11-08 21:30:56.683830

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bdfc319ce74e'
down_revision: Union[str, Sequence[str], None] = '26fa5d400443'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ref_dpd_status (
            status_code SMALLINT PRIMARY KEY,
            status_name TEXT NOT NULL UNIQUE
        )
    """)
    op.execute("""
        INSERT INTO ref_dpd_status (status_code, status_name) VALUES
            (1,  'Approved'),
            (2,  'Marketed'),
            (3,  'Cancelled Pre Market'),
            (4,  'Cancelled Post Market'),
            (6,  'Dormant'),
            (9,  'Cancelled (Unreturned Annual)'),
            (10, 'Cancelled (Safety Issue)'),
            (11, 'Authorized By Interim Order'),
            (12, 'Authorization By Interim Order Revoked'),
            (13, 'Restricted Access'),
            (14, 'Authorization By Interim Order Expired'),
            (15, 'Cancelled (Transitioned to Biocides)')
    """)
    op.execute("""
        CREATE TABLE drug_product (
            drug_code INT PRIMARY KEY,
            din TEXT CHECK (din IS NULL OR din ~ '^[0-9]{8}$'),
            brand_name TEXT NOT NULL,
            company_name TEXT,
            class_name TEXT,
            descriptor TEXT,
            number_of_ais INT,
            ai_group_no TEXT,
            last_update_date DATE,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX ux_drug_product_din ON drug_product (din) WHERE din IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_drug_product_ai_group ON drug_product (ai_group_no)")
    op.execute("""
        CREATE TABLE product_status (
            drug_code INT NOT NULL REFERENCES drug_product (drug_code) ON DELETE CASCADE,
            status_code SMALLINT NOT NULL REFERENCES ref_dpd_status (status_code),
            is_current BOOLEAN NOT NULL DEFAULT TRUE,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (drug_code, status_code)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE product_status")
    op.execute("DROP TABLE drug_product")
    op.execute("DROP TABLE ref_dpd_status")
