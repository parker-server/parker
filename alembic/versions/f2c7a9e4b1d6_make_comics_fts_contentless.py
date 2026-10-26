"""Rebuild comics_fts as a contentless FTS5 table

Revision ID: f2c7a9e4b1d6
Revises: e8b4d2a6f1c9
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2c7a9e4b1d6"
down_revision: Union[str, None] = "e8b4d2a6f1c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# contentless_delete=1 (needed so the delete and update triggers work) arrived in SQLite 3.43.0.
CONTENTLESS_DELETE_MIN_VERSION = (3, 43, 0)


def _sqlite_version(bind) -> tuple[int, ...]:
    version = bind.execute(sa.text("SELECT sqlite_version()")).scalar()
    return tuple(int(part) for part in version.split("."))


def upgrade() -> None:
    # Search only reads rowid (app/services/search.py), so the stored copy of
    # title/series/summary was dead weight: about 55 MB on an 80k-comic library.
    op.execute("DROP TABLE IF EXISTS comics_fts")

    if _sqlite_version(op.get_bind()) >= CONTENTLESS_DELETE_MIN_VERSION:
        op.execute(
            """
            CREATE VIRTUAL TABLE comics_fts USING fts5(
                title,
                series,
                summary,
                content='',
                contentless_delete=1
            );
            """
        )
    else:
        # Older SQLite can't delete from a contentless table, so keep stored content there.
        op.execute(
            """
            CREATE VIRTUAL TABLE comics_fts USING fts5(
                title,
                series,
                summary
            );
            """
        )

    op.execute("DROP TRIGGER IF EXISTS comics_fts_ins")
    op.execute("DROP TRIGGER IF EXISTS comics_fts_del")
    op.execute("DROP TRIGGER IF EXISTS comics_fts_upd")

    # Same trigger SQL as 712993eca5c4 and e8b4d2a6f1c9. The update trigger sets all
    # three columns, which a contentless_delete table requires.
    # On SQLite, any future batch_alter_table("comics") drops these and must recreate them.
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
    # Nothing to undo: a contentless index answers the same searches,
    # and 712993eca5c4's downgrade drops comics_fts either way.
    pass
