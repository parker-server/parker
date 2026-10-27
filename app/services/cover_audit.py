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

To test sorting changes without the archives, save every archive's file list
once and replay it later (with --compare to list the covers a change moves):
    python -m app.services.cover_audit --save-pages storage/pages.jsonl.gz
    python -m app.services.cover_audit --replay storage/pages.jsonl.gz --compare storage/covers.csv

To check flagged covers by eye, build a contact sheet from a finished report.
It shows each flagged archive's chosen cover next to the pages it might have
picked instead, and writes storage/cover_audit_sheet/index.html:
    python -m app.services.cover_audit --contact-sheet storage/cover_audit.csv

After a sorting change, mark only the comics whose cover changed so the thumbnail
task (Admin > Tasks) regenerates them instead of the whole library:
    python -m app.services.cover_audit --mark-thumbnails storage/cover_changes.csv
"""
import argparse
import csv
import gzip
import html
import json
import logging
import os
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.config import settings
from app.core.path_utils import resolve_absolute_path
from app.database import SessionLocal
from app.models.comic import Comic
from app.models.library_root import LibraryRoot
from app.services.archive import (
    ComicArchive,
    PageRole,
    _natural_sort_tuple,
    _page_sort_score,
    _page_stem_key,
    _split_archive_path,
    sort_pages,
)
from app.services.images import _allow_truncated_image_loads

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
# Stay well under SQLite's limit on query parameters
MARK_BATCH_SIZE = 500
SHEET_PROGRESS_EVERY = 100
SHEET_THUMB_SIZE = (200, 300)
SHEET_MAX_ALTERNATIVES = 3
# Most likely to hold a wrong cover first
SHEET_FLAG_ORDER = ["reordered", "cover_in_stray_folder", "multiple_cover_candidates"]


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


def read_archive_files(path: Path) -> list[str]:
    with ComicArchive(path) as archive:
        return archive.get_file_list()


def read_page_lists(pages_file: Path) -> list[tuple[str, list[str]]]:
    """Archive paths and raw file lists saved by --save-pages."""
    with gzip.open(pages_file, "rt", encoding="utf-8") as file:
        return [(record["path"], record["files"]) for record in map(json.loads, file)]


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
    save_pages: Path | None = None,
    replay: Path | None = None,
) -> int:
    # Each entry is (archive path, file list), with the file list read lazily unless replaying
    entries: list[tuple[str, list[str] | None]]
    if replay:
        entries = list(read_page_lists(replay))
        print(f"Replaying {len(entries)} saved page lists...", file=sys.stderr)
    else:
        entries = [(str(path), None) for path in find_archives(roots)]
        print(f"Auditing {len(entries)} archives...", file=sys.stderr)

    # Read before writing so --compare and --snapshot can point at the same file
    before = read_snapshot(compare) if compare else None

    output.parent.mkdir(parents=True, exist_ok=True)
    flag_counts: dict[str, int] = {}
    flagged = 0
    covers: dict[str, str] = {}
    pages_file = None
    if save_pages:
        save_pages.parent.mkdir(parents=True, exist_ok=True)
        pages_file = gzip.open(save_pages, "wt", encoding="utf-8")

    # utf-8-sig so spreadsheet apps read non-ASCII filenames correctly
    with output.open("w", newline="", encoding="utf-8-sig") as report:
        writer = csv.writer(report)
        writer.writerow(REPORT_COLUMNS)

        for count, (path, files) in enumerate(entries, start=1):
            try:
                if files is None:
                    files = read_archive_files(Path(path))
                pages = sort_pages(files)
                audit, page_count = audit_pages(pages), len(pages)
                if pages_file:
                    pages_file.write(json.dumps({"path": path, "files": files}) + "\n")
            except Exception as exc:
                audit, page_count = CoverAudit("", "", f"{type(exc).__name__}: {exc}", ("unreadable",)), 0

            covers[path] = audit.chosen_cover
            if audit.flags:
                flagged += 1
                for flag in audit.flags:
                    flag_counts[flag] = flag_counts.get(flag, 0) + 1
                writer.writerow([
                    path,
                    " ".join(audit.flags),
                    audit.chosen_cover,
                    audit.plain_first_page,
                    audit.reason,
                    page_count,
                ])

            if count % PROGRESS_EVERY == 0 and not replay:
                print(f"  {count}/{len(entries)} checked, {flagged} flagged", file=sys.stderr)

    if pages_file:
        pages_file.close()
        print(f"Page lists written to {save_pages.resolve()}", file=sys.stderr)

    print(f"Done. {flagged} of {len(entries)} archives flagged.", file=sys.stderr)
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


def mark_changed_for_thumbnails(db: Session, changes: Path) -> tuple[int, list[str]]:
    """
    Flag the comics listed in a cover_changes.csv as needing new thumbnails, so the
    regular thumbnail task regenerates just them. Returns how many were marked and
    the listed paths that match no comic in the library.
    """
    with changes.open(newline="", encoding="utf-8-sig") as file:
        listed = {os.path.normpath(row["path"]): row["path"] for row in csv.DictReader(file)}
    wanted = set(listed)

    rows = (
        db.query(Comic.id, LibraryRoot.path, Comic.relative_path)
        .join(LibraryRoot, Comic.library_root_id == LibraryRoot.id)
        .all()
    )
    found = {}
    for comic_id, root_path, relative_path in rows:
        path = os.path.normpath(resolve_absolute_path(root_path, relative_path))
        if path in wanted:
            found[path] = comic_id
    comic_ids = list(found.values())
    for start in range(0, len(comic_ids), MARK_BATCH_SIZE):
        batch = comic_ids[start:start + MARK_BATCH_SIZE]
        db.query(Comic).filter(Comic.id.in_(batch)).update({Comic.is_dirty: True}, synchronize_session=False)
    db.commit()
    return len(comic_ids), sorted(listed[path] for path in wanted - set(found))


def alternative_pages(pages: list[str]) -> list[tuple[str, str]]:
    """
    Pages worth showing next to the chosen cover (pages[0]), as (label, page):
    the first page by plain filename order, other pages that look like covers,
    then the next page as a fallback.
    """
    if len(pages) < 2:
        return []

    page_stems = {_page_stem_key(page) for page in pages}
    candidates = [("Plain first page", min(pages, key=lambda page: _natural_sort_tuple(page.lower())))]
    candidates += [
        ("Cover candidate", page)
        for page in pages[1:]
        if _page_sort_score(page, page_stems).role in COVER_ROLES
    ]
    candidates.append(("Next page", pages[1]))

    alternatives = []
    seen = {pages[0]}
    for label, page in candidates:
        if page not in seen:
            seen.add(page)
            alternatives.append((label, page))
    return alternatives[:SHEET_MAX_ALTERNATIVES]


def write_sheet_thumbnail(image_bytes: bytes, output: Path) -> bool:
    try:
        with _allow_truncated_image_loads():
            img = Image.open(BytesIO(image_bytes))
            # Lets JPEG decode at a reduced size, which is much faster for full-size scans
            img.draft("RGB", (SHEET_THUMB_SIZE[0] * 2, SHEET_THUMB_SIZE[1] * 2))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail(SHEET_THUMB_SIZE, Image.Resampling.LANCZOS)
            img.save(output, format="JPEG", quality=75)
        return True
    except Exception:
        return False


def _sheet_sort_key(row: dict[str, str]) -> tuple[int, str]:
    flags = row["flags"].split()
    ranks = [SHEET_FLAG_ORDER.index(flag) for flag in flags if flag in SHEET_FLAG_ORDER]
    return (min(ranks) if ranks else len(SHEET_FLAG_ORDER), row["path"])


def _sheet_card(index: int, row: dict[str, str], images: list[tuple[str, str, str | None]]) -> str:
    path = Path(row["path"])
    figures = []
    for label, page, image_name in images:
        preview = (
            f'<img src="img/{html.escape(image_name)}" alt="" loading="lazy">'
            if image_name
            else '<div class="missing">No preview</div>'
        )
        figures.append(
            f'<figure class="{"chosen" if label == "Chosen cover" else ""}">{preview}'
            f'<figcaption><strong>{html.escape(label)}</strong>'
            f'<span title="{html.escape(page)}">{html.escape(_split_archive_path(page)[1])}</span></figcaption></figure>'
        )
    chips = "".join(f'<span class="chip">{html.escape(flag)}</span>' for flag in row["flags"].split())
    return (
        f'<article class="card" data-flags="{html.escape(row["flags"])}" '
        f'data-path="{html.escape(row["path"])}" data-cover="{html.escape(images[0][1])}">'
        f'<header><h2 title="{html.escape(row["path"])}">{index}. {html.escape(path.name)}</h2>'
        f'<p class="folder">{html.escape(str(path.parent))}</p>'
        f'<p>{chips}<span class="reason">{html.escape(row["reason"])}</span></p></header>'
        f'<div class="pages">{"".join(figures)}</div>'
        f'<label class="wrong"><input type="checkbox"> Wrong cover</label></article>'
    )


def _sheet_html(cards: list[str], flag_names: list[str]) -> str:
    options = "".join(f'<option value="{html.escape(flag)}">{html.escape(flag)}</option>' for flag in flag_names)
    return SHEET_TEMPLATE.replace("{{count}}", str(len(cards))).replace("{{options}}", options).replace(
        "{{cards}}", "\n".join(cards)
    )


def make_contact_sheet(report: Path, sheet_dir: Path) -> int:
    with report.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    # A cover_changes.csv from --compare shows each new cover next to the previous one
    for row in rows:
        if "after_cover" in row:
            row.update(chosen_cover=row["after_cover"], flags="changed", reason="cover changed")
    rows = [row for row in rows if row["chosen_cover"]]
    rows.sort(key=_sheet_sort_key)
    print(f"Building contact sheet for {len(rows)} archives...", file=sys.stderr)

    image_dir = sheet_dir / "img"
    image_dir.mkdir(parents=True, exist_ok=True)
    for old_image in image_dir.glob("*.jpg"):
        old_image.unlink()

    cards = []
    skipped = 0
    flag_names: set[str] = set()
    for index, row in enumerate(rows, start=1):
        try:
            with ComicArchive(Path(row["path"])) as archive:
                pages = archive.get_pages()
                if not pages:
                    raise ValueError("archive has no pages")
                shown = [("Chosen cover", pages[0])]
                previous = row.get("before_cover")
                if previous and previous != pages[0] and previous in pages:
                    shown.append(("Previous cover", previous))
                shown_pages = {page for _, page in shown}
                shown += [alt for alt in alternative_pages(pages) if alt[1] not in shown_pages]
                shown = shown[:1 + SHEET_MAX_ALTERNATIVES]
                images = []
                for position, (label, page) in enumerate(shown):
                    image_name = f"{index}_{position}.jpg"
                    if not write_sheet_thumbnail(archive.read_file(page), image_dir / image_name):
                        image_name = None
                    images.append((label, page, image_name))
        except Exception as exc:
            skipped += 1
            print(f"  Skipped {row['path']}: {exc}", file=sys.stderr)
            continue

        flag_names.update(row["flags"].split())
        cards.append(_sheet_card(index, row, images))
        if index % SHEET_PROGRESS_EVERY == 0:
            print(f"  {index}/{len(rows)} done", file=sys.stderr)

    ordered_flags = [flag for flag in SHEET_FLAG_ORDER if flag in flag_names]
    ordered_flags += sorted(flag_names - set(SHEET_FLAG_ORDER))
    sheet = sheet_dir / "index.html"
    sheet.write_text(_sheet_html(cards, ordered_flags), encoding="utf-8")
    print(f"Done. {len(cards)} archives on the sheet, {skipped} skipped.", file=sys.stderr)
    print(f"Contact sheet written to {sheet.resolve()}", file=sys.stderr)
    return len(cards)


SHEET_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cover Audit Sheet</title>
<style>
  :root { --bg: #f6f6f4; --card: #fff; --text: #1d1d1b; --muted: #6b6b66; --line: #deded9; --accent: #2f6fde; --chip: #ecebe6; --wrong: #fdecea; --wrong-line: #d93025; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161615; --card: #20201f; --text: #ececea; --muted: #a0a09a; --line: #34342f; --accent: #7aa7f5; --chip: #2d2d2a; --wrong: #3a1f1d; --wrong-line: #f28b82; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.4 system-ui, sans-serif; }
  .bar { position: sticky; top: 0; z-index: 1; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; padding: 12px 16px; background: var(--card); border-bottom: 1px solid var(--line); }
  .bar h1 { margin: 0; font-size: 16px; }
  .bar .count { color: var(--muted); }
  .bar select, .bar button { font: inherit; padding: 4px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--text); }
  .bar button { background: var(--accent); border-color: var(--accent); color: #fff; cursor: pointer; }
  #copied { width: 100%; height: 6em; display: none; font: 12px monospace; }
  main { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 460px), 1fr)); gap: 12px; padding: 16px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-width: 0; }
  .card.is-wrong { background: var(--wrong); border-color: var(--wrong-line); }
  .card h2 { margin: 0; font-size: 14px; overflow-wrap: anywhere; }
  .card p { margin: 2px 0; }
  .folder { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
  .chip { display: inline-block; margin-right: 4px; padding: 0 6px; border-radius: 4px; background: var(--chip); font-size: 12px; }
  .reason { color: var(--muted); font-size: 12px; }
  .pages { display: flex; gap: 8px; margin: 8px 0; overflow-x: auto; }
  figure { margin: 0; width: 120px; flex: none; }
  figure img, .missing { display: block; width: 120px; height: 180px; object-fit: contain; background: var(--chip); border-radius: 4px; }
  .missing { display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 12px; }
  figure.chosen img { outline: 3px solid var(--accent); }
  figcaption { font-size: 11px; overflow-wrap: anywhere; }
  figcaption strong, figcaption span { display: block; }
  figcaption span { color: var(--muted); }
  .wrong { cursor: pointer; user-select: none; }
  .hidden { display: none; }
</style>
</head>
<body>
<div class="bar">
  <h1>Cover audit</h1>
  <span class="count">{{count}} archives, chosen cover outlined</span>
  <select id="filter"><option value="">All flags</option>{{options}}</select>
  <button id="copy" type="button">Copy wrong covers (0)</button>
  <textarea id="copied" readonly></textarea>
</div>
<main>
{{cards}}
</main>
<script>
  // Each sheet keeps its own ticks, so a changes sheet does not inherit the audit sheet's
  const KEY = "parker-cover-audit-wrong:" + location.pathname;
  const cards = [...document.querySelectorAll(".card")];
  let wrong = new Set();
  try { wrong = new Set(JSON.parse(localStorage.getItem(KEY) || "[]")); } catch (e) {}

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify([...wrong])); } catch (e) {}
    document.getElementById("copy").textContent = `Copy wrong covers (${wrong.size})`;
  }

  for (const card of cards) {
    const box = card.querySelector("input");
    box.checked = wrong.has(card.dataset.path);
    card.classList.toggle("is-wrong", box.checked);
    box.addEventListener("change", () => {
      box.checked ? wrong.add(card.dataset.path) : wrong.delete(card.dataset.path);
      card.classList.toggle("is-wrong", box.checked);
      save();
    });
  }
  save();

  document.getElementById("filter").addEventListener("change", (event) => {
    const flag = event.target.value;
    for (const card of cards) {
      card.classList.toggle("hidden", flag !== "" && !card.dataset.flags.split(" ").includes(flag));
    }
  });

  document.getElementById("copy").addEventListener("click", () => {
    const text = cards
      .filter((card) => wrong.has(card.dataset.path))
      .map((card) => `${card.dataset.path}\\t${card.dataset.cover}`)
      .join("\\n");
    const output = document.getElementById("copied");
    output.value = text;
    output.style.display = "block";
    output.select();
    if (navigator.clipboard) { navigator.clipboard.writeText(text).catch(() => {}); }
  });
</script>
</body>
</html>
"""


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
    parser.add_argument(
        "--contact-sheet",
        type=Path,
        metavar="REPORT",
        help="Instead of auditing, build a visual contact sheet from a finished report or cover_changes.csv (written to <name>_sheet/ next to it)",
    )
    parser.add_argument(
        "--save-pages",
        type=Path,
        metavar="PAGES",
        help="Also save every archive's file list to this gzipped file, for a later --replay",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        metavar="PAGES",
        help="Audit file lists saved by --save-pages instead of opening archives",
    )
    parser.add_argument(
        "--mark-thumbnails",
        type=Path,
        metavar="CHANGES",
        help="Instead of auditing, flag the comics in this cover_changes.csv for the thumbnail task (Admin > Tasks)",
    )
    args = parser.parse_args(argv)

    # Mislabeled-archive warnings would drown out the progress output
    logging.basicConfig(level=logging.ERROR)
    if args.mark_thumbnails:
        with SessionLocal() as db:
            marked, not_found = mark_changed_for_thumbnails(db, args.mark_thumbnails)
        print(f"Marked {marked} comics for new thumbnails ({len(not_found)} paths not found in the library).", file=sys.stderr)
        for path in not_found:
            print(f"  not found: {path}", file=sys.stderr)
        print("Run the thumbnail task from Admin > Tasks to regenerate them.", file=sys.stderr)
        return
    if args.contact_sheet:
        make_contact_sheet(args.contact_sheet, args.contact_sheet.with_name(f"{args.contact_sheet.stem}_sheet"))
        return
    run_audit(args.paths, args.output, args.snapshot, args.compare, args.save_pages, args.replay)


if __name__ == "__main__":
    main()
