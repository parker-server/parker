"""add external review cache

Revision ID: 8c2a4f6d9b10
Revises: a6b4c8d2e1f0
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c2a4f6d9b10"
down_revision: Union[str, None] = "a6b4c8d2e1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_review_lookups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("comic_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_issue_url", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["comic_id"], ["comics.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comic_id", "provider", name="uq_external_review_lookup_comic_provider"),
    )
    op.create_index(op.f("ix_external_review_lookups_id"), "external_review_lookups", ["id"], unique=False)
    op.create_index(op.f("ix_external_review_lookups_comic_id"), "external_review_lookups", ["comic_id"], unique=False)
    op.create_index(op.f("ix_external_review_lookups_provider"), "external_review_lookups", ["provider"], unique=False)
    op.create_index(op.f("ix_external_review_lookups_status"), "external_review_lookups", ["status"], unique=False)
    op.create_index(
        op.f("ix_external_review_lookups_next_retry_at"),
        "external_review_lookups",
        ["next_retry_at"],
        unique=False,
    )
    op.create_index(
        "idx_external_review_lookup_status_retry",
        "external_review_lookups",
        ["status", "next_retry_at"],
        unique=False,
    )

    op.create_table(
        "external_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lookup_id", sa.Integer(), nullable=False),
        sa.Column("review_type", sa.String(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("full_review_url", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["lookup_id"], ["external_review_lookups.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_external_reviews_id"), "external_reviews", ["id"], unique=False)
    op.create_index(op.f("ix_external_reviews_lookup_id"), "external_reviews", ["lookup_id"], unique=False)
    op.create_index(op.f("ix_external_reviews_review_type"), "external_reviews", ["review_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_external_reviews_review_type"), table_name="external_reviews")
    op.drop_index(op.f("ix_external_reviews_lookup_id"), table_name="external_reviews")
    op.drop_index(op.f("ix_external_reviews_id"), table_name="external_reviews")
    op.drop_table("external_reviews")

    op.drop_index("idx_external_review_lookup_status_retry", table_name="external_review_lookups")
    op.drop_index(op.f("ix_external_review_lookups_next_retry_at"), table_name="external_review_lookups")
    op.drop_index(op.f("ix_external_review_lookups_status"), table_name="external_review_lookups")
    op.drop_index(op.f("ix_external_review_lookups_provider"), table_name="external_review_lookups")
    op.drop_index(op.f("ix_external_review_lookups_comic_id"), table_name="external_review_lookups")
    op.drop_index(op.f("ix_external_review_lookups_id"), table_name="external_review_lookups")
    op.drop_table("external_review_lookups")
