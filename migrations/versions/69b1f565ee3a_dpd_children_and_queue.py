"""dpd children and queue

Revision ID: 69b1f565ee3a
Revises: bdfc319ce74e
Create Date: 2026-11-08 21:32:04.258768

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69b1f565ee3a'
down_revision: Union[str, Sequence[str], None] = 'bdfc319ce74e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE active_ingredient (
            ingredient_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            drug_code INT NOT NULL REFERENCES drug_product (drug_code) ON DELETE CASCADE,
            ingredient_name TEXT NOT NULL,
            ingredient_base TEXT NOT NULL,
            ingredient_qualifier TEXT,
            strength TEXT,
            strength_unit TEXT,
            strength_num NUMERIC CHECK (strength_num IS NULL OR strength_num >= 0),
            dosage_value TEXT,
            dosage_unit TEXT,
            dosage_value_num NUMERIC CHECK (dosage_value_num IS NULL OR dosage_value_num > 0),
            dosage_value_imputed BOOLEAN NOT NULL DEFAULT FALSE,
            conc_per_unit NUMERIC,
            conc_unit TEXT,
            row_hash CHAR(64) NOT NULL,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            UNIQUE (drug_code, row_hash)
        )
    """)
    op.execute("CREATE INDEX ix_active_ingredient_drug ON active_ingredient (drug_code)")
    op.execute("CREATE INDEX ix_active_ingredient_base ON active_ingredient (ingredient_base)")

    op.execute("""
        CREATE TABLE dosage_form (
            drug_code INT NOT NULL REFERENCES drug_product (drug_code) ON DELETE CASCADE,
            form_code INT NOT NULL,
            form_name TEXT NOT NULL,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            PRIMARY KEY (drug_code, form_code)
        )
    """)

    op.execute("""
        CREATE TABLE route (
            drug_code INT NOT NULL REFERENCES drug_product (drug_code) ON DELETE CASCADE,
            route_code INT NOT NULL,
            route_name TEXT NOT NULL,
            snapshot_id BIGINT NOT NULL REFERENCES raw_snapshot (snapshot_id),
            PRIMARY KEY (drug_code, route_code)
        )
    """)

    op.execute("""
        CREATE TABLE enrichment_queue (
            drug_code INT NOT NULL REFERENCES drug_product (drug_code) ON DELETE CASCADE,
            endpoint TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending', 'in_progress', 'done', 'failed')),
            attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (drug_code, endpoint)
        )
    """)
    op.execute(
        "CREATE INDEX ix_enrichment_queue_work ON enrichment_queue (state, next_attempt_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE enrichment_queue")
    op.execute("DROP TABLE route")
    op.execute("DROP TABLE dosage_form")
    op.execute("DROP TABLE active_ingredient")
