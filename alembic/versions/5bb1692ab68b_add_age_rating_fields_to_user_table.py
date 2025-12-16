"""Add age rating fields to user table

Revision ID: 5bb1692ab68b
Revises: f6c333d647a5
Create Date: 2025-12-13 18:11:04.243970

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5bb1692ab68b'
down_revision: Union[str, None] = 'f6c333d647a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Using batch_alter_table for SQLite compatibility
    with op.batch_alter_table('users', schema=None) as batch_op:
        # 1. Add max_age_rating (Nullable, defaults to None/No Restriction)
        batch_op.add_column(sa.Column('max_age_rating', sa.String(), nullable=True))

        # 2. Add allow_unknown (Not Null, defaults to False/0 for existing users)
        # We use server_default=sa.text('0') to ensure existing rows are set to False
        batch_op.add_column(
            sa.Column('allow_unknown_age_ratings', sa.Boolean(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('allow_unknown_age_ratings')
        batch_op.drop_column('max_age_rating')