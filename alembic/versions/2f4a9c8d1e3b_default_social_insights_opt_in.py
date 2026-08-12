"""Default social insights to enabled for new users

Revision ID: 2f4a9c8d1e3b
Revises: e1d5f6a7b8c9
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f4a9c8d1e3b"
down_revision: Union[str, None] = "e1d5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "share_progress_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "share_progress_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )
