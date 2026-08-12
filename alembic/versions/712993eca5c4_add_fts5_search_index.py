"""Add FTS5 search index

Revision ID: 712993eca5c4
Revises: 8f4012fb169f
Create Date: 2025-12-10 16:13:23.643312

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '712993eca5c4'
down_revision: Union[str, None] = '8f4012fb169f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # 1. Create the Virtual Table (FTS5)
    # We create a standalone table that stores copies of the text we want to search.
    # content_rowid='id' maps the FTS rowid directly to our Comic ID for fast lookups.
    op.execute("""
        CREATE VIRTUAL TABLE comics_fts USING fts5(
            title, 
            series, 
            summary, 
            content_rowid='id'
        );
    """)

    # 2. Trigger: INSERT
    # When a comic is added, look up its Series Name and insert into the index.
    # We JOIN Volume and Series to get the name.
    op.execute("""
               CREATE TRIGGER comics_fts_ins
                   AFTER INSERT
                   ON comics
               BEGIN
                   INSERT INTO comics_fts(rowid, title, series, summary)
                   VALUES (new.id,
                           new.title,
                           (SELECT s.name
                            FROM series s
                                     JOIN volumes v ON s.id = v.series_id
                            WHERE v.id = new.volume_id),
                           new.summary);
               END;
               """)

    # 3. Trigger: DELETE
    # When a comic is deleted, remove it from the index.
    op.execute("""
               CREATE TRIGGER comics_fts_del
                   AFTER DELETE
                   ON comics
               BEGIN
                   DELETE FROM comics_fts WHERE rowid = old.id;
               END;
               """)

    # 4. Trigger: UPDATE
    # When a comic is updated, update the index.
    # We re-query the Series name in case the comic was moved to a different volume.
    op.execute("""
               CREATE TRIGGER comics_fts_upd
                   AFTER UPDATE
                   ON comics
               BEGIN
                   UPDATE comics_fts
                   SET title   = new.title,
                       series  = (SELECT s.name
                                  FROM series s
                                           JOIN volumes v ON s.id = v.series_id
                                  WHERE v.id = new.volume_id),
                       summary = new.summary
                   WHERE rowid = old.id;
               END;
               """)

    # 5. Backfill / Initial Population
    # Populate the index with all existing comics currently in the database.
    op.execute("""
               INSERT INTO comics_fts(rowid, title, series, summary)
               SELECT c.id,
                      c.title,
                      s.name,
                      c.summary
               FROM comics c
                        JOIN volumes v ON c.volume_id = v.id
                        JOIN series s ON v.series_id = s.id;
               """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS comics_fts_upd")
    op.execute("DROP TRIGGER IF EXISTS comics_fts_del")
    op.execute("DROP TRIGGER IF EXISTS comics_fts_ins")
    op.execute("DROP TABLE IF EXISTS comics_fts")
