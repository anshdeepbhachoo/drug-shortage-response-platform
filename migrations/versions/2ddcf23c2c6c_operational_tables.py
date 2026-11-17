"""operational tables

Revision ID: 2ddcf23c2c6c
Revises: 4fd60fc25a1a
Create Date: 2026-11-17 02:00:00.000000

Tables for the hospital network's own data (synthetic for now, see
docs/SYNTHETIC_DATA.md), filled by ingestion/synthetic_loader.py and read by
the agents through services/agents/db_data.py:

    site, tracked_drug, supplier, inventory_snapshot, usage_record,
    substitution, purchase_order

Also adds the foreign keys the platform tables were waiting for: events and
plan actions now have to name a site and drug that exist.

Operational tables join to drug_product / shortage_report by DIN with no
foreign key, because synthetic DINs (9900xxxx) are not in the drug database.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "2ddcf23c2c6c"
down_revision: Union[str, Sequence[str], None] = "4fd60fc25a1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE site (
            site_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            region TEXT
        )
    """)

    op.execute("""
        CREATE TABLE tracked_drug (
            din TEXT PRIMARY KEY CHECK (din ~ '^[0-9]{8}$'),
            name TEXT NOT NULL,
            criticality TEXT NOT NULL CHECK (criticality IN ('critical', 'high', 'medium', 'low')),
            unit TEXT,
            drug_code INT,
            source_ref TEXT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE supplier (
            supplier_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL DEFAULT 'manual'
                    CHECK (channel IN ('edi', 'api', 'email', 'manual')),
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)

    # One row per site, drug and day: the progression's daily snapshots become history.
    op.execute("""
        CREATE TABLE inventory_snapshot (
            site_id TEXT NOT NULL REFERENCES site (site_id),
            din TEXT NOT NULL REFERENCES tracked_drug (din),
            on_hand INT NOT NULL CHECK (on_hand >= 0),
            as_of DATE NOT NULL,
            source_ref TEXT NOT NULL,
            PRIMARY KEY (site_id, din, as_of)
        )
    """)

    # One row per day (window_days = 1), or an average over a window
    # (window_days > 1, units_used = average per day), as in usage.csv.
    op.execute("""
        CREATE TABLE usage_record (
            site_id TEXT NOT NULL REFERENCES site (site_id),
            din TEXT NOT NULL REFERENCES tracked_drug (din),
            usage_date DATE NOT NULL,
            units_used NUMERIC NOT NULL CHECK (units_used >= 0),
            window_days INT NOT NULL DEFAULT 1 CHECK (window_days >= 1),
            source_ref TEXT NOT NULL,
            PRIMARY KEY (site_id, din, usage_date)
        )
    """)

    op.execute("""
        CREATE TABLE substitution (
            din TEXT NOT NULL REFERENCES tracked_drug (din),
            substitute_din TEXT NOT NULL CHECK (substitute_din ~ '^[0-9]{8}$'),
            note TEXT,
            source TEXT NOT NULL,
            approved BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (din, substitute_din),
            CHECK (din <> substitute_din)
        )
    """)

    op.execute("""
        CREATE TABLE purchase_order (
            order_id TEXT PRIMARY KEY,
            din TEXT NOT NULL REFERENCES tracked_drug (din),
            site_id TEXT NOT NULL REFERENCES site (site_id),
            supplier_id TEXT REFERENCES supplier (supplier_id),
            qty INT NOT NULL CHECK (qty > 0),
            unit_cost NUMERIC CHECK (unit_cost IS NULL OR unit_cost >= 0),
            status TEXT NOT NULL DEFAULT 'placed'
                   CHECK (status IN ('draft', 'pending_approval', 'approved', 'placed',
                                     'in_transit', 'delivered', 'delayed', 'cancelled')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            promised_date DATE,
            expected_date DATE,
            delivered_at DATE,
            event_id TEXT REFERENCES supply_event (event_id),
            plan_id TEXT REFERENCES plan (plan_id),
            source_ref TEXT NOT NULL,
            CHECK (status <> 'delivered' OR delivered_at IS NOT NULL),
            CHECK (status NOT IN ('approved', 'placed', 'in_transit', 'delivered', 'delayed')
                   OR supplier_id IS NOT NULL)
        )
    """)

    op.execute("CREATE INDEX ix_inventory_snapshot_din ON inventory_snapshot (din, as_of DESC)")
    op.execute("CREATE INDEX ix_usage_record_din ON usage_record (din, usage_date DESC)")
    op.execute("CREATE INDEX ix_purchase_order_din_status ON purchase_order (din, status)")

    # Platform tables: now that sites and drugs exist, require them.
    op.execute("ALTER TABLE supply_event ADD CONSTRAINT fk_supply_event_drug FOREIGN KEY (din) REFERENCES tracked_drug (din)")
    op.execute("ALTER TABLE supply_event ADD CONSTRAINT fk_supply_event_site FOREIGN KEY (site_id) REFERENCES site (site_id)")
    op.execute("ALTER TABLE plan_action ADD CONSTRAINT fk_plan_action_from FOREIGN KEY (from_site) REFERENCES site (site_id)")
    op.execute("ALTER TABLE plan_action ADD CONSTRAINT fk_plan_action_to FOREIGN KEY (to_site) REFERENCES site (site_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE plan_action DROP CONSTRAINT fk_plan_action_to")
    op.execute("ALTER TABLE plan_action DROP CONSTRAINT fk_plan_action_from")
    op.execute("ALTER TABLE supply_event DROP CONSTRAINT fk_supply_event_site")
    op.execute("ALTER TABLE supply_event DROP CONSTRAINT fk_supply_event_drug")
    op.execute("DROP TABLE purchase_order")
    op.execute("DROP TABLE substitution")
    op.execute("DROP TABLE usage_record")
    op.execute("DROP TABLE inventory_snapshot")
    op.execute("DROP TABLE supplier")
    op.execute("DROP TABLE tracked_drug")
    op.execute("DROP TABLE site")
