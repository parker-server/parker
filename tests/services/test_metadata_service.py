import json

from app.models.comic import Comic, Volume
from app.models.collection import Collection, CollectionItem
from app.models.library import Library
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.services.metadata import rehydrate_library_metadata_from_cache


def test_rehydrate_library_metadata_from_cache_restores_expected_metadata(db):
    library = Library(name="metadata-rehydrate-lib", path="/tmp/metadata-rehydrate-lib")
    series = Series(name="Metadata Rehydrate Series", library=library)
    volume = Volume(series=series, volume_number=1)

    restorable = Comic(
        volume=volume,
        number="1",
        title="Restorable Issue",
        filename="restorable.cbz",
        file_path="/tmp/restorable.cbz",
        metadata_json=json.dumps(
            {
                "alternate_series": "Event Gamma",
                "alternate_number": "3",
                "series_group": "Group Gamma",
                "story_arc": "Arc Gamma",
            }
        ),
    )

    missing_source = Comic(
        volume=volume,
        number="2",
        title="Missing Source",
        filename="missing-source.cbz",
        file_path="/tmp/missing-source.cbz",
        metadata_json=None,
    )

    db.add_all([library, series, volume, restorable, missing_source])
    db.commit()

    summary = rehydrate_library_metadata_from_cache(
        db=db,
        library_id=library.id,
        rehydrate_reading_lists=True,
        rehydrate_collections=True,
        rehydrate_story_arcs=True,
    )

    assert summary["comics_scanned"] == 2
    assert summary["reading_lists_restored"] == 1
    assert summary["collections_restored"] == 1
    assert summary["story_arcs_restored"] == 1
    assert summary["source_metadata_missing"] == 1
    assert summary["source_metadata_invalid"] == 0
    assert summary["force_scan_recommended"] is True

    db.refresh(restorable)
    db.refresh(missing_source)

    assert restorable.alternate_series == "Event Gamma"
    assert restorable.alternate_number == "3"
    assert restorable.series_group == "Group Gamma"
    assert restorable.story_arc == "Arc Gamma"

    assert missing_source.alternate_series is None
    assert missing_source.alternate_number is None
    assert missing_source.series_group is None
    assert missing_source.story_arc is None

    reading_list = db.query(ReadingList).filter(ReadingList.name == "Event Gamma").first()
    collection = db.query(Collection).filter(Collection.name == "Group Gamma").first()
    assert reading_list is not None
    assert collection is not None
    assert db.query(ReadingListItem).filter(
        ReadingListItem.reading_list_id == reading_list.id,
        ReadingListItem.comic_id == restorable.id,
    ).count() == 1
    assert db.query(CollectionItem).filter(
        CollectionItem.collection_id == collection.id,
        CollectionItem.comic_id == restorable.id,
    ).count() == 1
