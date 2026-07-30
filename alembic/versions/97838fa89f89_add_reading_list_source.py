"""add_reading_list_source

Revision ID: 97838fa89f89
Revises: 9def8df8ed7c
Create Date: 2026-07-27 19:09:36.391724

Replaces the old auto_generated int flag with an explicit `source` field so
reading lists can distinguish "manual" (user-owned), "comicinfo" (derived from
embedded AlternateSeries/AlternateNumber), and -- in a follow-up migration --
"cbl" (derived from a managed .cbl file) provenance. auto_generated=1 rows
become "comicinfo", auto_generated=0 rows become "manual"; NULL is treated as
"manual" conservatively, matching how the old integer flag defaulted callers
that never set it explicitly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97838fa89f89'
down_revision: Union[str, None] = '9def8df8ed7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


reading_lists = sa.table(
    "reading_lists",
    sa.column("id", sa.Integer),
    sa.column("auto_generated", sa.Integer),
    sa.column("source", sa.String),
)


def upgrade() -> None:
    with op.batch_alter_table("reading_lists", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(), nullable=True))

    conn = op.get_bind()
    conn.execute(reading_lists.update().where(reading_lists.c.auto_generated == 1).values(source="comicinfo"))
    conn.execute(reading_lists.update().where(reading_lists.c.auto_generated != 1).values(source="manual"))
    conn.execute(reading_lists.update().where(reading_lists.c.auto_generated.is_(None)).values(source="manual"))

    with op.batch_alter_table("reading_lists", schema=None) as batch_op:
        batch_op.alter_column("source", existing_type=sa.String(), nullable=False)
        batch_op.create_index(batch_op.f("ix_reading_lists_source"), ["source"])
        batch_op.drop_column("auto_generated")


def downgrade() -> None:
    with op.batch_alter_table("reading_lists", schema=None) as batch_op:
        batch_op.add_column(sa.Column("auto_generated", sa.Integer(), nullable=True))

    conn = op.get_bind()
    conn.execute(reading_lists.update().where(reading_lists.c.source == "comicinfo").values(auto_generated=1))
    conn.execute(reading_lists.update().where(reading_lists.c.source != "comicinfo").values(auto_generated=0))

    with op.batch_alter_table("reading_lists", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_reading_lists_source"))
        batch_op.drop_column("source")
