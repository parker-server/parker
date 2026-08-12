"""Add composite volume-age rating index to comics table

Revision ID: ce38f3d2bc82
Revises: c009bc77e8a6
Create Date: 2025-12-17 11:21:20.626719

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce38f3d2bc82'
down_revision: Union[str, None] = 'c009bc77e8a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('comics', schema=None) as batch_op:

        # Composite Index: Speeds up "Poison Pill" Security Checks
        # Allows checking "Does Volume X have a Mature book?" without reading the table.
        batch_op.create_index(
            'idx_comic_volume_age_rating',
            ['volume_id', 'age_rating'],
            unique=False
        )

def downgrade() -> None:
    with op.batch_alter_table('comics', schema=None) as batch_op:
        batch_op.drop_index('idx_comic_volume_age_rating')
