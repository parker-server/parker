"""Add generic target fields to scan_jobs

Revision ID: b8e2f4a6c1d3
Revises: a7d3e5f1c2b9
Create Date: 2026-10-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e2f4a6c1d3"
down_revision: Union[str, None] = "a7d3e5f1c2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("scan_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_type", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("target_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("payload_json", sa.Text(), nullable=True))

    # Only one active external review lookup per target.
    op.create_index(
        "uq_scan_jobs_active_external_review",
        "scan_jobs",
        ["job_type", "target_type", "target_id"],
        unique=True,
        sqlite_where=sa.text("job_type = 'external_review_lookup' AND status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_scan_jobs_active_external_review", table_name="scan_jobs")

    with op.batch_alter_table("scan_jobs", schema=None) as batch_op:
        batch_op.drop_column("payload_json")
        batch_op.drop_column("target_id")
        batch_op.drop_column("target_type")
