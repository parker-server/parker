"""add_library_roots_table

Revision ID: d4a1f6e8b3c2
Revises: 3a6f4d2b9c10
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union
import os
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4a1f6e8b3c2"
down_revision: Union[str, None] = "3a6f4d2b9c10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_for_match(path: str) -> str:
    # Mirrors app/api/libraries.py:_normalize_library_path. Used only to decide
    # whether file_path lives under a root, not for computing relative_path,
    # since normcase() folds case and would corrupt it on case-sensitive filesystems.
    return os.path.normcase(os.path.normpath(path.strip()))


def _normalize_preserve_case(path: str) -> str:
    return os.path.normpath(path.strip())


def upgrade() -> None:
    op.create_table(
        "library_roots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
        sa.Column("last_scan_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["library_id"], ["libraries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("library_roots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_library_roots_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_library_roots_library_id"), ["library_id"], unique=False)

    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.add_column(sa.Column("library_root_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("relative_path", sa.String(), nullable=True))
        batch_op.create_index(batch_op.f("ix_comics_library_root_id"), ["library_root_id"], unique=False)
        batch_op.create_index(
            "idx_comic_library_root_relative_path", ["library_root_id", "relative_path"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_comics_library_root_id", "library_roots", ["library_root_id"], ["id"]
        )

    # --- Backfill: one root per library, then match each comic to it by relative path ---
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    libraries = bind.execute(sa.text("SELECT id, path FROM libraries")).fetchall()

    library_root_ids: dict[int, tuple[int, str]] = {}
    for lib_id, lib_path in libraries:
        result = bind.execute(
            sa.text(
                """
                INSERT INTO library_roots (library_id, path, is_active, created_at, updated_at)
                VALUES (:library_id, :path, 1, :created_at, :updated_at)
                """
            ),
            {"library_id": lib_id, "path": lib_path, "created_at": now, "updated_at": now},
        )
        root_id = result.lastrowid
        library_root_ids[lib_id] = (root_id, lib_path)

    comics = bind.execute(
        sa.text(
            """
            SELECT comics.id, comics.file_path, series.library_id
            FROM comics
            JOIN volumes ON comics.volume_id = volumes.id
            JOIN series ON volumes.series_id = series.id
            """
        )
    ).fetchall()

    updates = []
    unmatched = 0
    for comic_id, file_path, library_id in comics:
        root = library_root_ids.get(library_id)
        if root is None:
            unmatched += 1
            continue

        root_id, root_path = root

        match_root = _normalize_for_match(root_path)
        match_file = _normalize_for_match(file_path)
        case_root = _normalize_preserve_case(root_path)
        case_file = _normalize_preserve_case(file_path)

        if match_file == match_root:
            relative_path = ""
        elif match_file.startswith(match_root + os.sep):
            # case_root/case_file only ran through normpath() (separators normalized,
            # case preserved). Slicing case_file at len(case_root) is safe because
            # normcase() — applied on top of normpath() to build match_root/match_file —
            # only folds case and does not change string length, so len(case_root) ==
            # len(match_root): the same boundary the startswith() check just verified.
            # Store with '/' regardless of host OS — this ships via a Linux Docker image
            # but is also run loose on Windows, and a stored '\\' would be unusable
            # (read as a literal filename, not a subdirectory) on the other platform.
            relative_path = case_file[len(case_root):].lstrip("/\\").replace("\\", "/")
        else:
            unmatched += 1
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

    print(f"library relocation backfill: {unmatched} comic(s) could not be matched to a root")


def downgrade() -> None:
    with op.batch_alter_table("comics", schema=None) as batch_op:
        batch_op.drop_constraint("fk_comics_library_root_id", type_="foreignkey")
        batch_op.drop_index("idx_comic_library_root_relative_path")
        batch_op.drop_index(batch_op.f("ix_comics_library_root_id"))
        batch_op.drop_column("relative_path")
        batch_op.drop_column("library_root_id")

    with op.batch_alter_table("library_roots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_library_roots_library_id"))
        batch_op.drop_index(batch_op.f("ix_library_roots_id"))

    op.drop_table("library_roots")
