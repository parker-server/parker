"""Add comic cover overrides

Revision ID: a7d3e9f2c4b1
Revises: f2c7a9e4b1d6
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7d3e9f2c4b1"
down_revision: Union[str, None] = "f2c7a9e4b1d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("comics", sa.Column("cover_override_page", sa.String(), nullable=True))
    op.add_column("comics", sa.Column("cover_override_image", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("comics", "cover_override_image")
    op.drop_column("comics", "cover_override_page")
