"""add character and team comic indexes

Revision ID: d5a1b7c3e9f2
Revises: c8e2f4a6b1d3
Create Date: 2026-10-30 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d5a1b7c3e9f2"
down_revision: Union[str, None] = "c8e2f4a6b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The link tables' primary keys start with comic_id, so finding one character's or
    # team's comics scanned every link. These cover (subject, comic) lookups on their own.
    with op.batch_alter_table("comic_characters", schema=None) as batch_op:
        batch_op.create_index("idx_comic_characters_character_comic", ["character_id", "comic_id"], unique=False)

    with op.batch_alter_table("comic_teams", schema=None) as batch_op:
        batch_op.create_index("idx_comic_teams_team_comic", ["team_id", "comic_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("comic_teams", schema=None) as batch_op:
        batch_op.drop_index("idx_comic_teams_team_comic")

    with op.batch_alter_table("comic_characters", schema=None) as batch_op:
        batch_op.drop_index("idx_comic_characters_character_comic")
