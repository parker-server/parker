"""add_user_volume_follows_table

Revision ID: 9f1a2c6b4d10
Revises: 7d3f4a6c9b21
Create Date: 2026-07-17 11:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f1a2c6b4d10"
down_revision: Union[str, None] = "7d3f4a6c9b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_volume_follows",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("volume_id", sa.Integer(), nullable=False),
        sa.Column("followed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["volume_id"], ["volumes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "volume_id"),
    )


def downgrade() -> None:
    op.drop_table("user_volume_follows")
