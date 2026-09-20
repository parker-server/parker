"""add cover murals

Revision ID: 0d4e5f6a7b8c
Revises: 6a7b8c9d0e1f
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0d4e5f6a7b8c"
down_revision: Union[str, None] = "6a7b8c9d0e1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cover_murals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("canvas_width", sa.Integer(), nullable=False),
        sa.Column("canvas_height", sa.Integer(), nullable=False),
        sa.Column("grid_size", sa.Integer(), nullable=False),
        sa.Column("background_color", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cover_murals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cover_murals_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cover_murals_user_id"), ["user_id"], unique=False)

    op.create_table(
        "cover_mural_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mural_id", sa.Integer(), nullable=False),
        sa.Column("comic_id", sa.Integer(), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("rotation", sa.Float(), nullable=False),
        sa.Column("z_index", sa.Integer(), nullable=False),
        sa.Column("fit_mode", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["comic_id"], ["comics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mural_id"], ["cover_murals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cover_mural_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cover_mural_items_comic_id"), ["comic_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cover_mural_items_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cover_mural_items_mural_id"), ["mural_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("cover_mural_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cover_mural_items_mural_id"))
        batch_op.drop_index(batch_op.f("ix_cover_mural_items_id"))
        batch_op.drop_index(batch_op.f("ix_cover_mural_items_comic_id"))
    op.drop_table("cover_mural_items")

    with op.batch_alter_table("cover_murals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cover_murals_user_id"))
        batch_op.drop_index(batch_op.f("ix_cover_murals_id"))
    op.drop_table("cover_murals")
