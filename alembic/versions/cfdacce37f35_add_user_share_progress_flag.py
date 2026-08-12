"""add_user_share_progress_flag

Revision ID: cfdacce37f35
Revises: 712993eca5c4
Create Date: 2025-12-12 09:50:29.091168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfdacce37f35'
down_revision: Union[str, None] = '712993eca5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Default to FALSE (Opt-in) for privacy
    op.add_column('users', sa.Column('share_progress_enabled', sa.Boolean(), server_default='0', nullable=False))

def downgrade():
    op.drop_column('users', 'share_progress_enabled')