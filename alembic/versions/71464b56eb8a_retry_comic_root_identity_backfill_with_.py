"""retry_comic_root_identity_backfill_with_corrected_matching

Revision ID: 71464b56eb8a
Revises: d4a1f6e8b3c2
Create Date: 2026-07-23 14:55:15.753269

The backfill in d4a1f6e8b3c2 matched comics to their library root using
os.path.normcase()/os.path.normpath()/os.sep, which reflect whichever OS runs
the migration, not necessarily whichever OS wrote the stored path string. That
under-matched two shapes of otherwise-perfectly-valid data:

- backslash-separated paths backfilled (or read back) on a host whose native
  separator is '/', since POSIX normpath/normcase never treat '\\' as a
  separator or fold its case
- a library root configured at a filesystem root ("/" or "C:\\"), since
  match_root + os.sep never has anything to match against

Both are pure matching-logic bugs, not real data drift, so they can be safely
re-attempted here using the corrected, OS-independent, segment-based
comparison (see app/core/path_utils.py:compute_relative_path for the ongoing
runtime equivalent -- duplicated here rather than imported, since migrations
shouldnt not depend on application code that can change after this migration
ships).

This is intentionally enrichment-only: it fills previously-NULL identity
columns for comics whose corrected relative_path can be computed, and leaves
alone anything it still can't resolve. It never deletes a comic. Comics that
remain unmatched after this migration weren't hit by these bugs -- their
stored file_path genuinely doesn't resolve under their library's active root
anymore (e.g. the library path was edited, pre-Phase-1, and never rescanned
since). That class can only be safely resolved by an actual scan, which is
able to verify the root is currently reachable before deciding a comic is
really gone, something this migration deliberately does not attempt.
"""
from typing import List, Optional, Sequence, Union
import posixpath

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71464b56eb8a'
down_revision: Union[str, None] = 'd4a1f6e8b3c2'
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
    still_unmatched = 0
    for comic_id, file_path, root_id, root_path in rows:
        relative_path = _compute_relative_path(root_path, file_path)
        if relative_path is None:
            still_unmatched += 1
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

    print(
        f"library root identity backfill retry: matched {len(updates)} previously-unmatched "
        f"comic(s) with the corrected comparison; {still_unmatched} still unmatched "
        f"(need a rescan of their library, not a migration, to resolve)"
    )


def downgrade() -> None:
    # Pure data enrichment of previously-NULL columns -- there is no reliable way
    # to tell which rows this migration touched apart from ones that were already
    # correct before it ran, so there's nothing safe to revert to.
    pass
