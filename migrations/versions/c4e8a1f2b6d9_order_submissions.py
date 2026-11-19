"""order submissions

Revision ID: c4e8a1f2b6d9
Revises: 9d4f6b2e8a13
Create Date: 2026-11-18 16:00:00.000000

What the order orchestrator agent needs (services/agents/agents/ordering/):

- order_submission: one row per step the orchestrator took to place an approved
  purchase order: a hand-off to a channel sub-agent (the hospital's ERP, EDI, a
  supplier portal, or an email for procurement to send), a hold, or an escalation.
  It keeps the exact payload built for that target system, what the target answered,
  whether it was simulated, who chose this step and why (chosen_by: agent or rule;
  reason: the orchestrator's brief), and the sub-agent's report (outcome, summary,
  read_by, steps). One order can have several attempts.

  status: accepted       the target system took the order (the order is placed)
          rejected       the target refused it (the orchestrator tries the next channel)
          failed         the payload failed its code checks; nothing was submitted
          held           a guardrail stopped it (e.g. above the auto-submit limit) until
                         procurement releases it
          ready_to_send  an email drafted for a person to send (the order is placed
                         when they record it)
          escalated      no channel could place it; procurement places it by hand
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c4e8a1f2b6d9"
down_revision: Union[str, Sequence[str], None] = "9d4f6b2e8a13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE order_submission (
            submission_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES purchase_order (order_id),
            attempt INT NOT NULL CHECK (attempt >= 1),
            channel TEXT NOT NULL,
            target TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('simulate', 'live')),
            status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'failed', 'held', 'ready_to_send', 'escalated')),
            external_ref TEXT,
            payload JSONB,
            response JSONB,
            note TEXT,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            chosen_by TEXT NOT NULL DEFAULT 'rule' CHECK (chosen_by IN ('agent', 'rule')),
            reason TEXT,
            report JSONB,
            UNIQUE (order_id, attempt)
        )
    """)
    op.execute("CREATE INDEX ix_order_submission_order ON order_submission (order_id)")


def downgrade() -> None:
    op.execute("DROP TABLE order_submission")
