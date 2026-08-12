"""add_index_to_volume_series_id

Revision ID: bd41f00d0b85
Revises: 5466f87f7df4
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "bd41f00d0b85"
down_revision: Union[str, None] = "5466f87f7df4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("volumes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_volumes_series_id"), ["series_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("volumes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_volumes_series_id"))
