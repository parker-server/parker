"""Add index to age rating field

Revision ID: c009bc77e8a6
Revises: 5bb1692ab68b
Create Date: 2025-12-13 19:15:58.908014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c009bc77e8a6'
down_revision: Union[str, None] = '5bb1692ab68b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Creating an index makes filtering by age_rating almost instant
    with op.batch_alter_table('comics', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_comics_age_rating'),
            ['age_rating'],
            unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('comics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_comics_age_rating'))