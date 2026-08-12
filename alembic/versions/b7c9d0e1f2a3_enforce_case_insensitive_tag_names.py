"""Enforce case-insensitive tag names

Revision ID: b7c9d0e1f2a3
Revises: f3a9c1d2e4b6
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c9d0e1f2a3"
down_revision: Union[str, None] = "f3a9c1d2e4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TAG_TABLES = (
    ("characters", "comic_characters", "character_id", "ux_characters_name_lower"),
    ("teams", "comic_teams", "team_id", "ux_teams_name_lower"),
    ("locations", "comic_locations", "location_id", "ux_locations_name_lower"),
    ("genres", "comic_genres", "genre_id", "ux_genres_name_lower"),
)


def _merge_case_insensitive_duplicates(table_name: str, link_table: str, tag_id_column: str) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id, lower(name) AS normalized_name FROM {table_name} ORDER BY id")
    ).mappings().all()

    grouped_ids: dict[str, list[int]] = {}
    for row in rows:
        grouped_ids.setdefault(row["normalized_name"], []).append(row["id"])

    for tag_ids in grouped_ids.values():
        if len(tag_ids) < 2:
            continue

        canonical_id = tag_ids[0]
        for duplicate_id in tag_ids[1:]:
            connection.execute(
                sa.text(
                    f"""
                    INSERT INTO {link_table} (comic_id, {tag_id_column})
                    SELECT source_link.comic_id, :canonical_id
                    FROM {link_table} AS source_link
                    WHERE source_link.{tag_id_column} = :duplicate_id
                      AND NOT EXISTS (
                        SELECT 1
                        FROM {link_table} AS existing_link
                        WHERE existing_link.comic_id = source_link.comic_id
                          AND existing_link.{tag_id_column} = :canonical_id
                      )
                    """
                ),
                {"canonical_id": canonical_id, "duplicate_id": duplicate_id},
            )
            connection.execute(
                sa.text(f"DELETE FROM {link_table} WHERE {tag_id_column} = :duplicate_id"),
                {"duplicate_id": duplicate_id},
            )
            connection.execute(
                sa.text(f"DELETE FROM {table_name} WHERE id = :duplicate_id"),
                {"duplicate_id": duplicate_id},
            )


def upgrade() -> None:
    for table_name, link_table, tag_id_column, index_name in TAG_TABLES:
        _merge_case_insensitive_duplicates(table_name, link_table, tag_id_column)
        op.execute(sa.text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} (lower(name))"))


def downgrade() -> None:
    for _table_name, _link_table, _tag_id_column, index_name in TAG_TABLES:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))
