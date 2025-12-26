"""Add is_dirty field to comics  table

Revision ID: c7e0f246b9d2
Revises: 5c88adcc0502
Create Date: 2025-12-25 22:20:49.516927

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e0f246b9d2'
down_revision: Union[str, None] = '5c88adcc0502'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add column
    op.add_column('comics', sa.Column('is_dirty', sa.Boolean(), server_default='0', nullable=False))

    # Create the Index
    # This ensures 'is_dirty == True' lookups are O(log n) instead of O(n)
    op.create_index('ix_comics_is_dirty', 'comics', ['is_dirty'])

def downgrade() -> None:
    op.drop_index('ix_comics_is_dirty', table_name='comics')
    op.drop_column('comics', 'is_dirty')
