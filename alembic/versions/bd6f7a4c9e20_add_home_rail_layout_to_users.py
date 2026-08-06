"""Add home rail layout to users

Revision ID: bd6f7a4c9e20
Revises: 6601fdcd204e
Create Date: 2026-08-06 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bd6f7a4c9e20"
down_revision: Union[str, None] = "6601fdcd204e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("home_rail_layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("home_rail_layout")
