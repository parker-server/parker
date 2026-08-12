"""Add library metadata parsing flags

Revision ID: d2a3c1e79f4b
Revises: 914418cc464c
Create Date: 2026-03-15 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2a3c1e79f4b"
down_revision: Union[str, None] = "914418cc464c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "libraries",
        sa.Column("parse_reading_lists", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "libraries",
        sa.Column("parse_collections", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "libraries",
        sa.Column("parse_story_arcs", sa.Boolean(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("libraries", "parse_story_arcs")
    op.drop_column("libraries", "parse_collections")
    op.drop_column("libraries", "parse_reading_lists")
