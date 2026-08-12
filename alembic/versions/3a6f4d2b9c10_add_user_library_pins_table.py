"""add_user_library_pins_table

Revision ID: 3a6f4d2b9c10
Revises: 2f4a9c8d1e3b
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3a6f4d2b9c10"
down_revision: Union[str, None] = "2f4a9c8d1e3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_library_pins",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["libraries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "library_id"),
    )


def downgrade() -> None:
    op.drop_table("user_library_pins")
