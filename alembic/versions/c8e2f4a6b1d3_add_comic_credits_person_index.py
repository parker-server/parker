"""add comic credits person index

Revision ID: c8e2f4a6b1d3
Revises: a7d3e9f2c4b1
Create Date: 2026-10-30 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c8e2f4a6b1d3"
down_revision: Union[str, None] = "a7d3e9f2c4b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Creator pages look up every credit for one person. Without this index SQLite scans
    # all credits (or every comic) for each of those queries.
    with op.batch_alter_table("comic_credits", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_comic_credits_person_id"), ["person_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("comic_credits", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comic_credits_person_id"))
