"""add_summary_override_to_series_and_volumes

Revision ID: 914418cc464c
Revises: c7e0f246b9d2
Create Date: 2026-02-04 10:34:13.100713

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '914418cc464c'
down_revision: Union[str, None] = 'c7e0f246b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Handle Series table batch operation
    with op.batch_alter_table('series', schema=None) as batch_op:
        batch_op.add_column(sa.Column('summary_override', sa.Text(), nullable=True))

    # Handle Volumes table batch operation
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('summary_override', sa.Text(), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table('series', schema=None) as batch_op:
        batch_op.drop_column('summary_override')

    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.drop_column('summary_override')
