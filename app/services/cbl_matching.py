import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.comic_helpers import normalize_issue_number
from app.core.text_utils import normalize_title
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.services.cbl_parser import CBLEntry

# ComicRack-style CBL <Book> entries carry only Series/Number/Volume/Year/Format --
# no filesystem path or filename -- so path/filename matching tiers (mentioned as
# optional in docs/cbl-reading-list-support-scope.md) have nothing to match against
# for real-world CBL files and are intentionally not implemented here. Matching order:
#
# 1. series + number + volume + year -- only works when the library's Volume tagging
#    convention agrees with the CBL file's (both usually mean "year the run started").
# 2. series + number + year -- drops the volume comparison but keeps year. Needed
#    because plenty of libraries tag Volume as a plain sequential index (1, 2, 3...)
#    rather than a start year, which would otherwise never agree with a CBL file's
#    Volume value and silently degrade every match to tier 3.
# 3. series + number only, as a last resort when no year is available on either side.
#
# Each tier only ever uses values the tagger/CBL file actually asserted -- never an
# inferred/derived one -- so a wrong guess is never mistaken for a confident match.


@dataclass
class CBLMatchedEntry:
    entry: CBLEntry
    comic_id: int
    matched_by: str  # "metadata", "metadata_year", or "relaxed_metadata"


@dataclass
class CBLUnmatchedEntry:
    entry: CBLEntry
    reason: str  # "unmatched", "ambiguous", or "duplicate_target"


@dataclass
class CBLMatchResult:
    matched: list[CBLMatchedEntry] = field(default_factory=list)
    unmatched: list[CBLUnmatchedEntry] = field(default_factory=list)

    @property
    def ordered_comic_ids(self) -> list[int]:
        return [m.comic_id for m in self.matched]

    def summary_json(self) -> str:
        counts = {"metadata": 0, "metadata_year": 0, "relaxed_metadata": 0}
        for m in self.matched:
            counts[m.matched_by] = counts.get(m.matched_by, 0) + 1

        reasons = {"unmatched": 0, "ambiguous": 0, "duplicate_target": 0}
        for u in self.unmatched:
            reasons[u.reason] = reasons.get(u.reason, 0) + 1

        sample = [
            f"{u.entry.series or '?'} #{u.entry.number or '?'} ({u.reason})"
            for u in self.unmatched[:10]
        ]

        return json.dumps({
            "matched_by_metadata": counts["metadata"],
            "matched_by_metadata_year": counts["metadata_year"],
            "matched_by_relaxed_metadata": counts["relaxed_metadata"],
            "unmatched": reasons["unmatched"],
            "ambiguous": reasons["ambiguous"],
            "duplicate_target": reasons["duplicate_target"],
            "sample_unmatched": sample,
        })


def _entry_key_parts(entry: CBLEntry) -> tuple[str, str]:
    return normalize_title(entry.series or ""), normalize_issue_number(entry.number or "") or ""


def match_cbl_entries(db: Session, entries: list[CBLEntry]) -> CBLMatchResult:
    """
    Match parsed CBL entries against comics already in the database.

    Never guesses: an entry with more than one candidate is reported as
    "ambiguous" and skipped rather than picking one arbitrarily. The rest of
    the list still comes back usable.
    """
    rows = (
        db.query(
            Comic.id,
            Comic.number,
            Comic.year,
            Volume.volume_number,
            Series.name.label("series_name"),
        )
        .select_from(Comic)
        .join(Comic.volume)
        .join(Volume.series)
        .all()
    )

    strict_index: dict[tuple[str, str, str, str], set[int]] = {}
    year_index: dict[tuple[str, str, str], set[int]] = {}
    relaxed_index: dict[tuple[str, str], set[int]] = {}

    for row in rows:
        norm_series = normalize_title(row.series_name or "")
        norm_number = normalize_issue_number(row.number or "") or ""
        relaxed_key = (norm_series, norm_number)
        relaxed_index.setdefault(relaxed_key, set()).add(row.id)

        if row.year is not None:
            year_key = (norm_series, norm_number, str(row.year))
            year_index.setdefault(year_key, set()).add(row.id)

            if row.volume_number is not None:
                strict_key = (norm_series, norm_number, str(row.volume_number), str(row.year))
                strict_index.setdefault(strict_key, set()).add(row.id)

    result = CBLMatchResult()
    used_comic_ids: set[int] = set()

    for entry in entries:
        norm_series, norm_number = _entry_key_parts(entry)
        matched_comic_id = None
        matched_by = None

        if entry.volume and entry.year:
            strict_key = (norm_series, norm_number, str(entry.volume).strip(), str(entry.year).strip())
            strict_candidates = strict_index.get(strict_key, set())

            if len(strict_candidates) == 1:
                matched_comic_id = next(iter(strict_candidates))
                matched_by = "metadata"
            elif len(strict_candidates) > 1:
                result.unmatched.append(CBLUnmatchedEntry(entry=entry, reason="ambiguous"))
                continue

        if matched_comic_id is None and entry.year:
            year_key = (norm_series, norm_number, str(entry.year).strip())
            year_candidates = year_index.get(year_key, set())

            if len(year_candidates) == 1:
                matched_comic_id = next(iter(year_candidates))
                matched_by = "metadata_year"
            elif len(year_candidates) > 1:
                result.unmatched.append(CBLUnmatchedEntry(entry=entry, reason="ambiguous"))
                continue

        if matched_comic_id is None:
            relaxed_key = (norm_series, norm_number)
            relaxed_candidates = relaxed_index.get(relaxed_key, set())

            if len(relaxed_candidates) == 1:
                matched_comic_id = next(iter(relaxed_candidates))
                matched_by = "relaxed_metadata"
            elif len(relaxed_candidates) > 1:
                result.unmatched.append(CBLUnmatchedEntry(entry=entry, reason="ambiguous"))
                continue

        if matched_comic_id is None:
            result.unmatched.append(CBLUnmatchedEntry(entry=entry, reason="unmatched"))
            continue

        if matched_comic_id in used_comic_ids:
            result.unmatched.append(CBLUnmatchedEntry(entry=entry, reason="duplicate_target"))
            continue

        used_comic_ids.add(matched_comic_id)
        result.matched.append(CBLMatchedEntry(entry=entry, comic_id=matched_comic_id, matched_by=matched_by))

    return result
