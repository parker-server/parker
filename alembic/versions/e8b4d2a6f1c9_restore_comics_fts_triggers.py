"""Restore comics_fts triggers and rebuild the search index

Revision ID: e8b4d2a6f1c9
Revises: c5f1a9d3e7b2
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e8b4d2a6f1c9"
down_revision: Union[str, None] = "c5f1a9d3e7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# On SQLite, batch_alter_table("comics") rebuilds the table and drops its triggers.
# d4a1f6e8b3c2, 7e3ba96ed6dc and 9def8df8ed7c all did that after 712993eca5c4 added them,
# so comics added or changed since then never reached comics_fts.
# Any future batch_alter_table("comics") must recreate these triggers afterwards.
def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS comics_fts USING fts5(
            title,
            series,
            summary,
            content_rowid='id'
        );
        """
    )

    op.execute("DROP TRIGGER IF EXISTS comics_fts_ins")
    op.execute("DROP TRIGGER IF EXISTS comics_fts_del")
    op.execute("DROP TRIGGER IF EXISTS comics_fts_upd")

    # Same trigger SQL as 712993eca5c4.
    op.execute(
        """
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
        """
    )
    op.execute(
        """
        CREATE TRIGGER comics_fts_del
            AFTER DELETE
            ON comics
        BEGIN
            DELETE FROM comics_fts WHERE rowid = old.id;
        END;
        """
    )
    op.execute(
        """
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
        """
    )

    # Rebuild from scratch: drops rows for deleted comics, refreshes stale ones,
    # and adds the ones the missing triggers never indexed.
    # LEFT JOIN matches the insert trigger, which indexes a comic even without a series.
    op.execute("DELETE FROM comics_fts")
    op.execute(
        """
        INSERT INTO comics_fts(rowid, title, series, summary)
        SELECT c.id,
               c.title,
               s.name,
               c.summary
        FROM comics c
                 LEFT JOIN volumes v ON c.volume_id = v.id
                 LEFT JOIN series s ON v.series_id = s.id;
        """
    )


def downgrade() -> None:
    # Nothing to undo: the triggers and index belong to 712993eca5c4,
    # and removing them again would only bring back the stale index.
    pass
