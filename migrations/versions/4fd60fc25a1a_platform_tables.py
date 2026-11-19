"""platform tables

Revision ID: 4fd60fc25a1a
Revises: b7e8f9a0c1d2
Create Date: 2026-11-16 12:00:00.000000

Tables our code writes from validated agent output (agents never write):
    supply_event, plan, plan_action, decision, audit_log

The synthetic operational tables (sites, stock, usage, orders, suppliers,
substitutions) are a separate piece of work. Until they exist, DINs and site
IDs here are checked for format only, with no foreign keys; add the keys in a
later migration once those tables land.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "4fd60fc25a1a"
down_revision: Union[str, Sequence[str], None] = "b7e8f9a0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # Signal -> plan -> actions -> decision
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE supply_event (
            event_id TEXT PRIMARY KEY,
            din TEXT NOT NULL CHECK (din ~ '^[0-9]{8}$'),
            site_id TEXT,
            kind TEXT NOT NULL
                 CHECK (kind IN ('delay', 'shortage', 'usage_spike', 'discontinuation')),
            source_type TEXT NOT NULL
                        CHECK (source_type IN ('supplier_email', 'shortage_report', 'site_report')),
            source_ref TEXT NOT NULL,
            delay_days NUMERIC CHECK (delay_days IS NULL OR delay_days >= 0),
            confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'monitoring', 'mitigated', 'closed')),
            detail JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE plan (
            plan_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES supply_event (event_id),
            status TEXT NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed', 'pending_approval', 'approved',
                                     'rejected', 'broken', 'superseded')),
            no_perfect_plan BOOLEAN NOT NULL DEFAULT FALSE,
            source TEXT NOT NULL CHECK (source IN ('planning_agent', 'rules')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE plan_action (
            action_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES plan (plan_id),
            type TEXT NOT NULL
                 CHECK (type IN ('transfer', 'order', 'substitute', 'conserve', 'escalate')),
            description TEXT NOT NULL,
            reasoning TEXT,
            from_site TEXT,
            to_site TEXT,
            units INT CHECK (units IS NULL OR units > 0),
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            problems JSONB,
            CHECK (from_site IS NULL OR from_site <> to_site),
            CHECK (type <> 'transfer' OR (from_site IS NOT NULL AND to_site IS NOT NULL AND units IS NOT NULL))
        )
    """)

    op.execute("""
        CREATE TABLE decision (
            decision_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES plan (plan_id),
            actor TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
            note TEXT,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ---------------------------------------------------------------
    # Audit log: append-only, enforced in the database
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit_log (
            audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            at TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor TEXT NOT NULL,
            actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent', 'rule', 'system')),
            action TEXT NOT NULL,
            object TEXT,
            detail JSONB,
            refs TEXT[] NOT NULL DEFAULT '{}'
        )
    """)
    op.execute("""
        CREATE FUNCTION audit_log_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_change
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()
    """)
    # Row triggers don't fire on TRUNCATE, so block it separately.
    op.execute("""
        CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()
    """)

    op.execute("CREATE INDEX ix_supply_event_din ON supply_event (din, status)")
    op.execute("CREATE INDEX ix_plan_event ON plan (event_id)")
    op.execute("CREATE INDEX ix_plan_action_plan ON plan_action (plan_id)")
    op.execute("CREATE INDEX ix_decision_plan ON decision (plan_id)")
    op.execute("CREATE INDEX ix_audit_log_at ON audit_log (at)")


def downgrade() -> None:
    op.execute("DROP TABLE audit_log")
    op.execute("DROP FUNCTION audit_log_append_only()")
    op.execute("DROP TABLE decision")
    op.execute("DROP TABLE plan_action")
    op.execute("DROP TABLE plan")
    op.execute("DROP TABLE supply_event")
