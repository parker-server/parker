"""finalize_comic_root_identity_not_null

Revision ID: 7e3ba96ed6dc
Revises: 71464b56eb8a
Create Date: 2026-07-23 16:23:05.965312

This is the "close out the fallback" migration: it finalizes
(library_root_id, relative_path) as the sole physical identity for comics, so
application code no longer needs (and, after the next migration, no longer
has) a Comic.file_path column to fall back to.

Steps:
1. Retry the same corrected-match logic as 71464b56eb8a one more time (cheap,
   idempotent no-op if the database is already clean).
2. Delete any comic still unmatched after that -- logging id/filename/
   file_path so it's traceable. This is not a new class of data loss: a comic
   whose file can't be located under its library's root is already treated as
   gone everywhere else in this codebase (scanner cleanup, maintenance
   janitor); this just performs that resolution proactively at upgrade time
   instead of waiting on a scan that might never happen. See
   docs/library-relocation-scope.md for the full reasoning and the release
   note callout that accompanies this.
3. Make comics.library_root_id / comics.relative_path NOT NULL.
4. Upgrade idx_comic_library_root_relative_path from a plain index to a
   unique constraint, now that it is the sole physical identity -- carrying
   the same duplicate-prevention guarantee file_path's uniqueness used to
   provide. Nothing today should violate it (the app's own matching logic
   keys off this exact pair), so a conflict here means real corruption, and
   this migration deliberately lets that fail loudly rather than silently
   deduplicating.
5. Log (informational only) any library with zero active library_roots --
   not reachable through normal app flow, but worth surfacing rather than
   silently ignoring if it somehow exists.
"""
from typing import List, Optional, Sequence, Union
import posixpath

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e3ba96ed6dc'
down_revision: Union[str, None] = '71464b56eb8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _segments(path: str) -> List[str]:
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return [part for part in normalized.split("/") if part not in ("", ".")]


def _compute_relative_path(root_path: str, file_path: str) -> Optional[str]:
    root_segments = _segments(root_path)
    file_segments = _segments(file_path)

    if len(file_segments) < len(root_segments):
        return None

    prefix = file_segments[:len(root_segments)]
    if [part.lower() for part in prefix] != [part.lower() for part in root_segments]:
        return None

    return "/".join(file_segments[len(root_segments):])


def upgrade() -> None:
    bind = op.get_bind()

    # --- Step 1: retry matching for anything still unresolved ---
    rows = bind.execute(
        sa.text(
            """
            SELECT comics.id, comics.file_path, library_roots.id, library_roots.path
            FROM comics
            JOIN volumes ON comics.volume_id = volumes.id
            JOIN series ON volumes.series_id = series.id
            JOIN library_roots
                ON library_roots.library_id = series.library_id
                AND library_roots.is_active = 1
            WHERE comics.library_root_id IS NULL OR comics.relative_path IS NULL
            """
        )
    ).fetchall()

    updates = []
    for comic_id, file_path, root_id, root_path in rows:
        relative_path = _compute_relative_path(root_path, file_path)
        if relative_path is None:
            continue
        updates.append({"comic_id": comic_id, "library_root_id": root_id, "relative_path": relative_path})

    if updates:
        bind.execute(
            sa.text(
                """
                UPDATE comics
                SET library_root_id = :library_root_id, relative_path = :relative_path
                WHERE id = :comic_id
                """
            ),
            updates,
        )

    # --- Step 2: delete whatever still can't be resolved ---
    # Covers both "matched a root but relative_path still didn't compute" (rows
    # above) and "no active root joined at all" (e.g. an orphaned library) --
    # either way, nothing left to identify these comics by once file_path is gone.
    still_unresolved = bind.execute(
        sa.text(
            "SELECT id, filename, file_path FROM comics "
            "WHERE library_root_id IS NULL OR relative_path IS NULL"
        )
    ).fetchall()

    for comic_id, filename, file_path in still_unresolved:
        print(
            f"finalize backfill: deleting unresolvable comic id={comic_id} "
            f"filename={filename!r} file_path={file_path!r}"
        )

    if still_unresolved:
        unresolved_ids = [row[0] for row in still_unresolved]

        # This connection (like the rest of the app) never turns on `PRAGMA
        # foreign_keys`, so SQLite won't enforce ON DELETE CASCADE for a raw
        # SQL DELETE like this one -- and pull_list_items.comic_id doesn't
        # even declare ondelete=CASCADE in the model. Every table that
        # references comics.id needs an explicit delete here, since none of
        # the ORM-level cascade="all, delete-orphan" relationships that
        # normally cover this (for a `db.delete(comic)` call elsewhere in the
        # app) apply to raw SQL.
        dependent_tables = [
            "activity_log",
            "bookmarks",
            "collection_items",
            "comic_credits",
            "user_comic_ratings",
            "pull_list_items",
            "reading_list_items",
            "reading_progress",
            "comic_characters",
            "comic_teams",
            "comic_locations",
            "comic_genres",
        ]
        # Batched rather than one IN (...) with every id -- SQLite caps the
        # number of bound parameters per statement (SQLITE_MAX_VARIABLE_NUMBER,
        # 999 on older builds), which a large unresolved set could exceed.
        id_batches = [unresolved_ids[i:i + 500] for i in range(0, len(unresolved_ids), 500)]

        for table in dependent_tables:
            for batch in id_batches:
                bind.execute(
                    sa.text(f"DELETE FROM {table} WHERE comic_id IN :ids").bindparams(
                        sa.bindparam("ids", expanding=True)
                    ),
                    {"ids": batch},
                )

        for batch in id_batches:
            bind.execute(
                sa.text("DELETE FROM comics WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": batch},
            )

    print(
        f"finalize backfill: matched {len(updates)} previously-unmatched comic(s) with the "
        f"corrected comparison; deleted {len(still_unresolved)} comic(s) that could not be "
        f"resolved to a root"
    )

    # --- Step 5: surface (not block on) libraries with zero active roots ---
    orphan_libraries = bind.execute(
        sa.text(
            """
            SELECT libraries.id, libraries.name
            FROM libraries
            LEFT JOIN library_roots
                ON library_roots.library_id = libraries.id AND library_roots.is_active = 1
            WHERE library_roots.id IS NULL
            """
        )
    ).fetchall()

    for lib_id, lib_name in orphan_libraries:
        print(f"finalize backfill: library id={lib_id} name={lib_name!r} has no active root")

    # --- Step 3: NOT NULL ---
    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.alter_column("library_root_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("relative_path", existing_type=sa.String(), nullable=False)

    # --- Step 4: unique constraint ---
    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.drop_index("idx_comic_library_root_relative_path")
        batch_op.create_index(
            "idx_comic_library_root_relative_path",
            ["library_root_id", "relative_path"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.drop_index("idx_comic_library_root_relative_path")
        batch_op.create_index(
            "idx_comic_library_root_relative_path",
            ["library_root_id", "relative_path"],
            unique=False,
        )
        batch_op.alter_column("relative_path", existing_type=sa.String(), nullable=True)
        batch_op.alter_column("library_root_id", existing_type=sa.Integer(), nullable=True)

    # No reliable way to restore comics deleted during upgrade() -- same
    # limitation as 71464b56eb8a's downgrade.
