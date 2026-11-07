"""add_user_reading_list_pins_table

Revision ID: b7d3e5f9a2c4
Revises: a4e8c2f6d1b9
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7d3e5f9a2c4"
down_revision: Union[str, None] = "a4e8c2f6d1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_reading_list_pins",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reading_list_id", sa.Integer(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["reading_list_id"], ["reading_lists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "reading_list_id"),
    )


def downgrade() -> None:
    op.drop_table("user_reading_list_pins")
