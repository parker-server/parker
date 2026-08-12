"""Add monthly_reading_goal field to users table

Revision ID: 5c88adcc0502
Revises: 1b8db3505063
Create Date: 2025-12-22 20:35:02.172443

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c88adcc0502'
down_revision: Union[str, None] = '1b8db3505063'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Using batch_alter_table for SQLite compatibility
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('monthly_reading_goal', sa.Integer(), nullable=False, server_default='10'))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('monthly_reading_goal')
