"""
Find comic archives whose cover pick deserves a manual look.

Reads each archive's file list (no images are decoded) and flags archives where
the page Parker picks as the cover is not simply the first page by filename, or
where the pick was a close call. Writes the flagged archives to a CSV report.

Run inside the Parker container:
    docker compose exec parker python -m app.services.cover_audit

To see which covers a sorting change moves, save a snapshot before updating
Parker and compare against it afterwards:
    python -m app.services.cover_audit --snapshot storage/covers_before.csv
    python -m app.services.cover_audit --compare storage/covers_before.csv
"""
import argparse
import csv
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.services.archive import (
    ComicArchive,
    PageRole,
    _natural_sort_tuple,
    _page_sort_score,
    _page_stem_key,
    _split_archive_path,
)

SUPPORTED_EXTENSIONS = {".cbz", ".cbr"}
COVER_ROLES = {PageRole.COVER, PageRole.LIKELY_COVER}
DEFAULT_OUTPUT = Path("storage/cover_audit.csv")
PROGRESS_EVERY = 500
# A folder this small next to other folders usually holds a scanner tag, not the comic
STRAY_FOLDER_MAX_PAGES = 2
REPORT_COLUMNS = ["path", "flags", "chosen_cover", "plain_first_page", "reason", "page_count"]
SNAPSHOT_COLUMNS = ["path", "cover"]
CHANGES_COLUMNS = ["path", "before_cover", "after_cover"]
CHANGES_FILENAME = "cover_changes.csv"


@dataclass(frozen=True)
class CoverAudit:
    chosen_cover: str
    plain_first_page: str
    reason: str
    flags: tuple[str, ...]


def audit_pages(pages: list[str]) -> CoverAudit:
    """
    Audit an already sorted page list (as returned by ComicArchive.get_pages).

    Flags:
      reordered                  the cover is not the first page by plain filename order
      multiple_cover_candidates  more than one page looks like a cover
      cover_in_stray_folder      the cover sits in a tiny folder apart from the rest of the pages
      no_pages                   the archive has no image pages
    """
    if not pages:
        return CoverAudit("", "", "", ("no_pages",))

    page_stems = {_page_stem_key(page) for page in pages}
    scores = [_page_sort_score(page, page_stems) for page in pages]
    plain_first_page = min(pages, key=lambda page: _natural_sort_tuple(page.lower()))
    chosen_cover = pages[0]
    cover_score = scores[0]

    flags = []
    if chosen_cover != plain_first_page:
        flags.append("reordered")
    if sum(1 for score in scores if score.role in COVER_ROLES) > 1:
        flags.append("multiple_cover_candidates")
    folders = [_split_archive_path(page)[0] for page in pages]
    cover_folder_pages = folders.count(folders[0])
    if cover_folder_pages < len(pages) and cover_folder_pages <= STRAY_FOLDER_MAX_PAGES:
        flags.append("cover_in_stray_folder")

    reason = cover_score.cover_signal or cover_score.role.value
    return CoverAudit(chosen_cover, plain_first_page, reason, tuple(flags))


def find_archives(roots: list[Path]) -> list[Path]:
    archives = []
    for root in roots:
        for directory, _, filenames in os.walk(root):
            for filename in filenames:
                if Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS:
                    archives.append(Path(directory) / filename)
    return sorted(archives)


def audit_archive(path: Path) -> tuple[CoverAudit, int]:
    with ComicArchive(path) as archive:
        pages = archive.get_pages()
    return audit_pages(pages), len(pages)


def write_snapshot(snapshot: Path, covers: dict[str, str]) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with snapshot.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(SNAPSHOT_COLUMNS)
        writer.writerows(covers.items())


def read_snapshot(snapshot: Path) -> dict[str, str]:
    with snapshot.open(newline="", encoding="utf-8-sig") as file:
        return {row["path"]: row["cover"] for row in csv.DictReader(file)}


def changed_covers(before: dict[str, str], after: dict[str, str]) -> list[tuple[str, str, str]]:
    """Archives present in both snapshots whose cover changed, as (path, before, after)."""
    return [
        (path, before[path], cover)
        for path, cover in after.items()
        if path in before and before[path] != cover
    ]


def write_changes(changes_output: Path, changes: list[tuple[str, str, str]]) -> None:
    with changes_output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(CHANGES_COLUMNS)
        writer.writerows(changes)


def run_audit(
    roots: list[Path],
    output: Path,
    snapshot: Path | None = None,
    compare: Path | None = None,
) -> int:
    archives = find_archives(roots)
    print(f"Auditing {len(archives)} archives...", file=sys.stderr)

    # Read before writing so --compare and --snapshot can point at the same file
    before = read_snapshot(compare) if compare else None

    output.parent.mkdir(parents=True, exist_ok=True)
    flag_counts: dict[str, int] = {}
    flagged = 0
    covers: dict[str, str] = {}

    # utf-8-sig so spreadsheet apps read non-ASCII filenames correctly
    with output.open("w", newline="", encoding="utf-8-sig") as report:
        writer = csv.writer(report)
        writer.writerow(REPORT_COLUMNS)

        for count, path in enumerate(archives, start=1):
            try:
                audit, page_count = audit_archive(path)
            except Exception as exc:
                audit, page_count = CoverAudit("", "", f"{type(exc).__name__}: {exc}", ("unreadable",)), 0

            covers[str(path)] = audit.chosen_cover
            if audit.flags:
                flagged += 1
                for flag in audit.flags:
                    flag_counts[flag] = flag_counts.get(flag, 0) + 1
                writer.writerow([
                    str(path),
                    " ".join(audit.flags),
                    audit.chosen_cover,
                    audit.plain_first_page,
                    audit.reason,
                    page_count,
                ])

            if count % PROGRESS_EVERY == 0:
                print(f"  {count}/{len(archives)} checked, {flagged} flagged", file=sys.stderr)

    print(f"Done. {flagged} of {len(archives)} archives flagged.", file=sys.stderr)
    for flag, total in sorted(flag_counts.items()):
        print(f"  {flag}: {total}", file=sys.stderr)
    print(f"Report written to {output.resolve()}", file=sys.stderr)

    if before is not None:
        changes = changed_covers(before, covers)
        changes_output = output.with_name(CHANGES_FILENAME)
        write_changes(changes_output, changes)
        print(f"Covers changed since {compare}: {len(changes)}", file=sys.stderr)
        print(f"Changes written to {changes_output.resolve()}", file=sys.stderr)

    if snapshot:
        write_snapshot(snapshot, covers)
        print(f"Snapshot of {len(covers)} covers written to {snapshot.resolve()}", file=sys.stderr)

    return flagged


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Flag comic archives whose cover pick deserves a manual look.")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[settings.comics_path],
        help="Library folders to audit (default: COMICS_PATH)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"CSV report path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Save every archive's current cover to this CSV, for a later --compare",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help=f"List archives whose cover changed since this snapshot (written to {CHANGES_FILENAME} next to the report)",
    )
    args = parser.parse_args(argv)

    # Mislabeled-archive warnings would drown out the progress output
    logging.basicConfig(level=logging.ERROR)
    run_audit(args.paths, args.output, args.snapshot, args.compare)


if __name__ == "__main__":
    main()
