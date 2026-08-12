"""Add depends_on field to system_settings table

Revision ID: 5f2be68db998
Revises: ce38f3d2bc82
Create Date: 2025-12-18 13:52:41.025662

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f2be68db998'
down_revision: Union[str, None] = 'ce38f3d2bc82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('depends_on', sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.drop_column('depends_on')
