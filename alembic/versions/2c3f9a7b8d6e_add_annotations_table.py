"""Add annotations table

Revision ID: 2c3f9a7b8d6e
Revises: b7c9d0e1f2a3
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2c3f9a7b8d6e"
down_revision: Union[str, None] = "b7c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("comic_id", sa.Integer(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=32), nullable=False),
        sa.Column("anchor_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["comic_id"], ["comics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_annotations_comic_id"), "annotations", ["comic_id"], unique=False)
    op.create_index(op.f("ix_annotations_created_at"), "annotations", ["created_at"], unique=False)
    op.create_index(op.f("ix_annotations_id"), "annotations", ["id"], unique=False)
    op.create_index(op.f("ix_annotations_page_index"), "annotations", ["page_index"], unique=False)
    op.create_index(op.f("ix_annotations_user_id"), "annotations", ["user_id"], unique=False)
    op.create_index(
        "ix_annotations_user_comic_page",
        "annotations",
        ["user_id", "comic_id", "page_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_annotations_user_comic_page", table_name="annotations")
    op.drop_index(op.f("ix_annotations_user_id"), table_name="annotations")
    op.drop_index(op.f("ix_annotations_page_index"), table_name="annotations")
    op.drop_index(op.f("ix_annotations_id"), table_name="annotations")
    op.drop_index(op.f("ix_annotations_created_at"), table_name="annotations")
    op.drop_index(op.f("ix_annotations_comic_id"), table_name="annotations")
    op.drop_table("annotations")
