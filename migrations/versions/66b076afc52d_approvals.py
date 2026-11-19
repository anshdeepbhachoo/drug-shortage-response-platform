"""approvals

Revision ID: 66b076afc52d
Revises: 3e358a2c926a
Create Date: 2026-11-17 04:00:00.000000

What the approval step (services/agents/store/approvals.py) needs:

- plan_action.option_index: a plan holds alternative options; approving means
  choosing one, so each action records which option it belongs to. Existing
  rows are filled from their action IDs ("P-...-O2-M1" -> 2).
- plan.detail: the numbers the plan was checked against (shortfall, cover
  days), so approval can re-check transfers against current stock.
- decision.option_index + one decision per plan: which option a person
  approved; a rejection has none.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "66b076afc52d"
down_revision: Union[str, Sequence[str], None] = "3e358a2c926a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE plan_action ADD COLUMN option_index INT CHECK (option_index IS NULL OR option_index >= 1)")
    op.execute("""
        UPDATE plan_action
        SET option_index = substring(action_id FROM '-O([0-9]+)(-M[0-9]+)?$')::int
        WHERE option_index IS NULL
    """)
    op.execute("CREATE INDEX ix_plan_action_option ON plan_action (plan_id, option_index)")

    op.execute("ALTER TABLE plan ADD COLUMN detail JSONB")

    op.execute("ALTER TABLE decision ADD COLUMN option_index INT")
    op.execute("ALTER TABLE decision ADD CONSTRAINT uq_decision_plan UNIQUE (plan_id)")
    op.execute("""
        ALTER TABLE decision ADD CONSTRAINT ck_decision_option
        CHECK ((decision = 'approved') = (option_index IS NOT NULL))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE decision DROP CONSTRAINT ck_decision_option")
    op.execute("ALTER TABLE decision DROP CONSTRAINT uq_decision_plan")
    op.execute("ALTER TABLE decision DROP COLUMN option_index")
    op.execute("ALTER TABLE plan DROP COLUMN detail")
    op.execute("DROP INDEX ix_plan_action_option")
    op.execute("ALTER TABLE plan_action DROP COLUMN option_index")
