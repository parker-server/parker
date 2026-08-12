"""drop_comic_file_path_and_library_path

Revision ID: 9def8df8ed7c
Revises: 7e3ba96ed6dc
Create Date: 2026-07-23 16:29:22.235138

Schema-only cleanup: drops the two legacy compatibility columns now that
(library_root_id, relative_path) / (library_roots) fully replace them and
nothing in the application reads either column anymore. 7e3ba96ed6dc already
guaranteed every surviving comic has a usable (library_root_id,
relative_path); this migration just removes the now-unused columns.

comics.file_path had an inline UniqueConstraint (not a separately-named
index), so dropping the column via batch mode removes that constraint along
with it -- there's nothing left to name and drop separately.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9def8df8ed7c'
down_revision: Union[str, None] = '7e3ba96ed6dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.drop_column("file_path")

    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.drop_column("path")


def downgrade() -> None:
    # Values are gone for good -- this only restores the column shape (nullable,
    # since we have no data to backfill them with), not the data that once lived there.
    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("path", sa.String(), nullable=True))

    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.add_column(sa.Column("file_path", sa.String(), nullable=True))
