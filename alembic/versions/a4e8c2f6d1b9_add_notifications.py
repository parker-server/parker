"""add notifications

Revision ID: a4e8c2f6d1b9
Revises: d5a1b7c3e9f2
Create Date: 2026-10-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4e8c2f6d1b9'
down_revision: Union[str, None] = 'd5a1b7c3e9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('link', sa.String(), nullable=True),
        sa.Column('group_key', sa.String(), nullable=True),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index('idx_notifications_user_read', ['user_id', 'read_at'], unique=False)
        batch_op.create_index('idx_notifications_user_group', ['user_id', 'group_key'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notification_preferences', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('notification_preferences')

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index('idx_notifications_user_group')
        batch_op.drop_index('idx_notifications_user_read')

    op.drop_table('notifications')
