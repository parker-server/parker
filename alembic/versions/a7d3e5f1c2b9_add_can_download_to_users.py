"""Add can_download permission to users

Revision ID: a7d3e5f1c2b9
Revises: e4a7c9d2f1b8
Create Date: 2026-10-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d3e5f1c2b9"
down_revision: Union[str, None] = "e4a7c9d2f1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing users keep download access so OPDS readers keep working.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("can_download", sa.Boolean(), server_default=sa.text("1"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("can_download")
