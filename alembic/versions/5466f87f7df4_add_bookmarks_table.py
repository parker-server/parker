"""Add bookmarks table

Revision ID: 5466f87f7df4
Revises: 9f1a2c6b4d10
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5466f87f7df4"
down_revision: Union[str, None] = "9f1a2c6b4d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("comic_id", sa.Integer(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["comic_id"], ["comics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "comic_id", "page_index", name="unique_user_comic_bookmark_page"),
    )
    op.create_index(op.f("ix_bookmarks_comic_id"), "bookmarks", ["comic_id"], unique=False)
    op.create_index(op.f("ix_bookmarks_created_at"), "bookmarks", ["created_at"], unique=False)
    op.create_index(op.f("ix_bookmarks_id"), "bookmarks", ["id"], unique=False)
    op.create_index(op.f("ix_bookmarks_user_id"), "bookmarks", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bookmarks_user_id"), table_name="bookmarks")
    op.drop_index(op.f("ix_bookmarks_id"), table_name="bookmarks")
    op.drop_index(op.f("ix_bookmarks_created_at"), table_name="bookmarks")
    op.drop_index(op.f("ix_bookmarks_comic_id"), table_name="bookmarks")
    op.drop_table("bookmarks")
