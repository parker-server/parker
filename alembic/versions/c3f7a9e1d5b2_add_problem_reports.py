"""add problem reports

Revision ID: c3f7a9e1d5b2
Revises: b7d3e5f9a2c4
Create Date: 2026-11-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f7a9e1d5b2'
down_revision: Union[str, None] = 'b7d3e5f9a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'problem_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('comic_id', sa.Integer(), nullable=True),
        sa.Column('comic_label', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('page_index', sa.Integer(), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('file_changed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['comic_id'], ['comics.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('problem_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_problem_reports_comic_id'), ['comic_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_problem_reports_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_problem_reports_status'), ['status'], unique=False)
        batch_op.create_index('idx_problem_reports_comic_status', ['comic_id', 'status'], unique=False)

    # Everyone can report until an admin turns it off for a user.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('can_report', sa.Boolean(), server_default=sa.text('1'), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('can_report')

    with op.batch_alter_table('problem_reports', schema=None) as batch_op:
        batch_op.drop_index('idx_problem_reports_comic_status')
        batch_op.drop_index(batch_op.f('ix_problem_reports_status'))
        batch_op.drop_index(batch_op.f('ix_problem_reports_user_id'))
        batch_op.drop_index(batch_op.f('ix_problem_reports_comic_id'))

    op.drop_table('problem_reports')
