"""BOT sec review: job heartbeat table (R-6 / A-1)

Revision ID: 73a8ede3c7c4
Revises: 87d970971665
Create Date: 2026-11-09

Adds ``job_heartbeats``: one row per scheduled job, upserted by the job
itself after each run (see src/jobs/scheduled_alerts.py). The log-scan
job (src/jobs/log_scan.py) treats a row older than 36 h — or a job that
never wrote a row — as a dead man and alerts the Chairman, so a silently
dead cron is noticed instead of being discovered months later.

A plain create_table: no existing table is altered, so no batch mode is
needed (batch_alter_table only matters for ALTER on SQLite) and the
statement is identical on Postgres and SQLite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '73a8ede3c7c4'
down_revision: Union[str, Sequence[str], None] = '87d970971665'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_heartbeats",
        sa.Column("job_name", sa.String(length=80), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("job_name"),
    )


def downgrade() -> None:
    op.drop_table("job_heartbeats")
