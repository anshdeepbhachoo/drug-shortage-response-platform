"""agent pipeline checkpoints

Revision ID: 7c2e91d4b5a0
Revises: 3a5369ccfac1
Create Date: 2026-11-17 22:00:00.000000

Tables where the LangGraph agentic pipeline (services/pipeline/graph/pipeline.py) keeps
each alert's position in its workflow, so an alert can wait days for approval or follow-up
and resume where it stopped.

The schema is langgraph-checkpoint-postgres 3.2.1's own (its MIGRATIONS 0-9), created here
by the owner role instead of at runtime, and recorded in checkpoint_migrations so the saver's
setup() has nothing left to do. Indexes are created without CONCURRENTLY (the tables are new
and Alembic runs in a transaction). The app roles get access through the default privileges
in docker/postgres/init.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "7c2e91d4b5a0"
down_revision: Union[str, Sequence[str], None] = "3a5369ccfac1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SAVER_MIGRATIONS = 10  # langgraph-checkpoint-postgres 3.2.1


def upgrade() -> None:
    op.execute("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute("""
        CREATE TABLE checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint JSONB NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
    """)
    op.execute("""
        CREATE TABLE checkpoint_blobs (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            version TEXT NOT NULL,
            type TEXT NOT NULL,
            blob BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        )
    """)
    op.execute("""
        CREATE TABLE checkpoint_writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            blob BYTEA NOT NULL,
            task_path TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
    """)
    op.execute("CREATE INDEX checkpoints_thread_id_idx ON checkpoints (thread_id)")
    op.execute("CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs (thread_id)")
    op.execute("CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes (thread_id)")
    op.execute(f"INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, {SAVER_MIGRATIONS - 1})")


def downgrade() -> None:
    op.execute("DROP TABLE checkpoint_writes")
    op.execute("DROP TABLE checkpoint_blobs")
    op.execute("DROP TABLE checkpoints")
    op.execute("DROP TABLE checkpoint_migrations")
