"""Add administrator-managed promoted Home rails.

Revision ID: e4a7c9d2f1b8
Revises: f3c24a73d963
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4a7c9d2f1b8"
down_revision: Union[str, None] = "f3c24a73d963"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promoted_rails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("query_config", sa.JSON(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("promoted_rails", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_promoted_rails_id"), ["id"], unique=False)
        batch_op.create_index(
            "ix_promoted_rails_active_window",
            ["enabled", "starts_on", "ends_on"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("promoted_rails", schema=None) as batch_op:
        batch_op.drop_index("ix_promoted_rails_active_window")
        batch_op.drop_index(batch_op.f("ix_promoted_rails_id"))
    op.drop_table("promoted_rails")
