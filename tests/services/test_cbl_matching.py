import json

from app.models.comic import Volume
from app.models.series import Series
from app.services.cbl_matching import match_cbl_entries
from app.services.cbl_parser import CBLEntry
from tests.factories import create_comic, create_library_with_root


def _make_comic(db, library, series_name, *, number, volume_number=1, year=None, prefix):
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=volume_number)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        f"{prefix}.cbz",
        number=number,
        year=year,
        filename=f"{prefix}.cbz",
        page_count=1,
    )
    return comic


def test_match_unique_metadata_with_volume_and_year(db, tmp_path):
    library = create_library_with_root(db, "cbl-match-lib", str(tmp_path / "lib"))
    comic = _make_comic(db, library, "Infinity Gauntlet", number="1", volume_number=1991, year=1991, prefix="ig-1")
    db.commit()

    entries = [CBLEntry(series="The Infinity Gauntlet", number="1", volume="1991", year="1991")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == [comic.id]
    assert result.matched[0].matched_by == "metadata"
    assert result.unmatched == []


def test_match_falls_back_to_relaxed_when_entry_missing_volume_or_year(db, tmp_path):
    library = create_library_with_root(db, "cbl-relaxed-lib", str(tmp_path / "lib"))
    comic = _make_comic(db, library, "Silver Surfer", number="34", volume_number=1987, year=1990, prefix="ss-34")
    db.commit()

    # No Volume/Year on the CBL entry -- can't use the strict tier at all.
    entries = [CBLEntry(series="Silver Surfer", number="34")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == [comic.id]
    assert result.matched[0].matched_by == "relaxed_metadata"


def test_match_falls_back_to_relaxed_when_comic_missing_year(db, tmp_path):
    library = create_library_with_root(db, "cbl-no-year-lib", str(tmp_path / "lib"))
    # Comic has no year -- can never populate the strict index for this row.
    comic = _make_comic(db, library, "No Year Series", number="5", volume_number=1, year=None, prefix="ny-5")
    db.commit()

    entries = [CBLEntry(series="No Year Series", number="5", volume="1", year="2020")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == [comic.id]
    assert result.matched[0].matched_by == "relaxed_metadata"


def test_match_uses_metadata_year_tier_when_volume_tagging_conventions_differ(db, tmp_path):
    library = create_library_with_root(db, "cbl-sequential-volume-lib", str(tmp_path / "lib"))
    # Locally tagged with a sequential volume number (1, 2, 3...), not a start year --
    # a real-world convention mismatch against CBL files, which use Volume="1991"
    # (year-style) here. Strict tier can't agree on volume, but year alone still does.
    comic = _make_comic(
        db, library, "Infinity Gauntlet", number="1", volume_number=1, year=1991, prefix="ig-seq-1"
    )
    db.commit()

    entries = [CBLEntry(series="Infinity Gauntlet", number="1", volume="1991", year="1991")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == [comic.id]
    assert result.matched[0].matched_by == "metadata_year"


def test_match_ambiguous_at_metadata_year_tier_does_not_fall_through_to_relaxed(db, tmp_path):
    library = create_library_with_root(db, "cbl-ambiguous-year-lib", str(tmp_path / "lib"))
    # Two different volumes (reboots) sharing series + number + year -- exactly the
    # case tier 2 exists to disambiguate, but here it genuinely can't.
    _make_comic(db, library, "Ambig Year", number="1", volume_number=1, year=2020, prefix="ambig-year-a")
    _make_comic(db, library, "Ambig Year", number="1", volume_number=2, year=2020, prefix="ambig-year-b")
    db.commit()

    entries = [CBLEntry(series="Ambig Year", number="1", year="2020")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == []
    assert result.unmatched[0].reason == "ambiguous"


def test_match_reports_ambiguous_when_multiple_comics_share_strict_key(db, tmp_path):
    library = create_library_with_root(db, "cbl-ambiguous-lib", str(tmp_path / "lib"))
    _make_comic(db, library, "Dupes", number="1", volume_number=2020, year=2020, prefix="dupe-a")
    _make_comic(db, library, "Dupes", number="1", volume_number=2020, year=2020, prefix="dupe-b")
    db.commit()

    entries = [CBLEntry(series="Dupes", number="1", volume="2020", year="2020")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == []
    assert result.unmatched[0].reason == "ambiguous"


def test_match_reports_unmatched_when_no_comic_found(db, tmp_path):
    entries = [CBLEntry(series="Nonexistent Series", number="99")]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == []
    assert result.unmatched[0].reason == "unmatched"


def test_match_reports_duplicate_target_when_two_entries_resolve_to_same_comic(db, tmp_path):
    library = create_library_with_root(db, "cbl-dup-target-lib", str(tmp_path / "lib"))
    comic = _make_comic(db, library, "Solo Series", number="1", volume_number=1, year=2000, prefix="solo-1")
    db.commit()

    # Two differently-specified entries that both resolve to the same underlying comic.
    entries = [
        CBLEntry(series="Solo Series", number="1", volume="1", year="2000"),
        CBLEntry(series="Solo Series", number="1"),
    ]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == [comic.id]
    assert len(result.unmatched) == 1
    assert result.unmatched[0].reason == "duplicate_target"


def test_match_preserves_entry_order_in_ordered_comic_ids(db, tmp_path):
    library = create_library_with_root(db, "cbl-order-lib", str(tmp_path / "lib"))
    c1 = _make_comic(db, library, "Order Series", number="2", volume_number=1, year=2000, prefix="order-2")
    c2 = _make_comic(db, library, "Order Series", number="1", volume_number=1, year=2000, prefix="order-1")
    db.commit()

    entries = [
        CBLEntry(series="Order Series", number="2", volume="1", year="2000"),
        CBLEntry(series="Order Series", number="1", volume="1", year="2000"),
    ]
    result = match_cbl_entries(db, entries)

    assert result.ordered_comic_ids == [c1.id, c2.id]


def test_summary_json_reports_counts_and_sample():
    from app.services.cbl_matching import CBLMatchResult, CBLMatchedEntry, CBLUnmatchedEntry

    result = CBLMatchResult(
        matched=[
            CBLMatchedEntry(entry=CBLEntry(series="A", number="1"), comic_id=1, matched_by="metadata"),
            CBLMatchedEntry(entry=CBLEntry(series="B", number="2"), comic_id=2, matched_by="metadata_year"),
            CBLMatchedEntry(entry=CBLEntry(series="E", number="5"), comic_id=3, matched_by="relaxed_metadata"),
        ],
        unmatched=[
            CBLUnmatchedEntry(entry=CBLEntry(series="C", number="3"), reason="unmatched"),
            CBLUnmatchedEntry(entry=CBLEntry(series="D", number="4"), reason="ambiguous"),
        ],
    )

    payload = json.loads(result.summary_json())
    assert payload["matched_by_metadata"] == 1
    assert payload["matched_by_metadata_year"] == 1
    assert payload["matched_by_relaxed_metadata"] == 1
    assert payload["unmatched"] == 1
    assert payload["ambiguous"] == 1
    assert payload["duplicate_target"] == 0
    assert "C #3 (unmatched)" in payload["sample_unmatched"]
