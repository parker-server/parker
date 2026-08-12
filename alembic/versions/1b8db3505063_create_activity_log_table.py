"""Create activity_log table

Revision ID: 1b8db3505063
Revises: 5f2be68db998
Create Date: 2025-12-21 20:30:38.990437

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b8db3505063'
down_revision: Union[str, None] = '5f2be68db998'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the new table
    activity_log = op.create_table(
        'activity_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('comic_id', sa.Integer(), nullable=False),
        sa.Column('pages_read', sa.Integer(), nullable=False),
        sa.Column('start_page', sa.Integer(), nullable=False),
        sa.Column('end_page', sa.Integer(), nullable=False),
        sa.Column('context_type', sa.String(), nullable=True),
        sa.Column('context_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['comic_id'], ['comics.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )
    op.create_index(op.f('ix_activity_log_created_at'), 'activity_log', ['created_at'], unique=False)
    op.create_index(op.f('ix_activity_log_user_id'), 'activity_log', ['user_id'], unique=False)
    op.create_index(op.f('ix_activity_log_comic_id'), 'activity_log', ['comic_id'], unique=False)

    # 2. SEED DATA: Migrate existing ReadingProgress to ActivityLog
    # We create one log entry per progress record where current_page > 0.
    # We set start_page to 0 and end_page/pages_read to current_page.
    op.execute(
        """
        INSERT INTO activity_log (user_id, comic_id, pages_read, start_page, end_page, context_type, created_at)
        SELECT user_id, comic_id, current_page, 0, current_page, 'legacy', last_read_at
        FROM reading_progress
        WHERE current_page > 0
        """
    )



def downgrade() -> None:
    op.drop_table('activity_log')
