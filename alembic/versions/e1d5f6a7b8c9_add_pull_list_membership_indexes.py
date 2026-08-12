"""add pull list membership indexes

Revision ID: e1d5f6a7b8c9
Revises: bd41f00d0b85
Create Date: 2026-07-19 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e1d5f6a7b8c9"
down_revision: Union[str, None] = "bd41f00d0b85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pull_lists", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pull_lists_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("pull_list_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pull_list_items_comic_id"), ["comic_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("pull_list_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pull_list_items_comic_id"))

    with op.batch_alter_table("pull_lists", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pull_lists_user_id"))
