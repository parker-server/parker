"""make_job_library_id_nullable

Revision ID: 23bc0e2cee25
Revises: cfdacce37f35
Create Date: 2025-12-12 15:15:53.288905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23bc0e2cee25'
down_revision: Union[str, None] = 'cfdacce37f35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite requires 'batch_alter_table' to change column constraints
    with op.batch_alter_table('scan_jobs') as batch_op:
        batch_op.alter_column('library_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Note: This might fail if we have rows with NULLs, but that's expected for downgrades
    with op.batch_alter_table('scan_jobs') as batch_op:
        batch_op.alter_column('library_id', existing_type=sa.Integer(), nullable=False)
