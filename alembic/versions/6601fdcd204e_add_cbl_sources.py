"""add_cbl_sources

Revision ID: 6601fdcd204e
Revises: 97838fa89f89
Create Date: 2026-07-27 19:09:36.918623

Adds the cbl_sources table (managed .cbl files Parker has imported via
upload, URL, catalog, or library-root discovery) and links reading_lists to
the source that owns it, so a "cbl"-sourced reading list can be resynced or
torn down alongside its managed file.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6601fdcd204e'
down_revision: Union[str, None] = '97838fa89f89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cbl_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("stored_path", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("catalog_provider", sa.String(), nullable=True),
        sa.Column("catalog_path", sa.String(), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("last_refresh_status", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=True),
        sa.Column("last_match_summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cbl_sources_id"), "cbl_sources", ["id"])
    op.create_index(op.f("ix_cbl_sources_origin"), "cbl_sources", ["origin"])
    op.create_index(op.f("ix_cbl_sources_fingerprint"), "cbl_sources", ["fingerprint"])

    with op.batch_alter_table("reading_lists", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_cbl_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_reading_lists_source_cbl_id"), ["source_cbl_id"])
        batch_op.create_foreign_key(
            "fk_reading_lists_source_cbl_id_cbl_sources",
            "cbl_sources",
            ["source_cbl_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("reading_lists", schema=None) as batch_op:
        batch_op.drop_constraint("fk_reading_lists_source_cbl_id_cbl_sources", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_reading_lists_source_cbl_id"))
        batch_op.drop_column("source_cbl_id")

    op.drop_index(op.f("ix_cbl_sources_fingerprint"), table_name="cbl_sources")
    op.drop_index(op.f("ix_cbl_sources_origin"), table_name="cbl_sources")
    op.drop_index(op.f("ix_cbl_sources_id"), table_name="cbl_sources")
    op.drop_table("cbl_sources")
