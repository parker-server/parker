import importlib.util
import json
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app.models.comic import Comic, Volume
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.services.metadata import parse_comicinfo, rehydrate_library_metadata_from_cache
from tests.factories import create_library_with_root

STRIP_RAW_XML_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c5f1a9d3e7b2_strip_raw_xml_from_comic_metadata.py"
)


def _load_strip_raw_xml_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("strip_raw_xml_migration", STRIP_RAW_XML_MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_parse_comicinfo_keeps_rehydrate_fields_without_raw_xml():
    xml = (
        b"<ComicInfo>"
        b"<Series>Gamma</Series>"
        b"<AlternateSeries>Event Gamma</AlternateSeries>"
        b"<AlternateNumber>3</AlternateNumber>"
        b"<SeriesGroup>Group Gamma</SeriesGroup>"
        b"<StoryArc>Arc Gamma</StoryArc>"
        b"</ComicInfo>"
    )

    parsed = parse_comicinfo(xml)

    assert "raw_xml" not in parsed
    assert parsed["series"] == "Gamma"
    assert parsed["alternate_series"] == "Event Gamma"
    assert parsed["alternate_number"] == "3"
    assert parsed["series_group"] == "Group Gamma"
    assert parsed["story_arc"] == "Arc Gamma"


def test_comic_metadata_json_is_not_loaded_by_default(db):
    library = create_library_with_root(db, "metadata-defer-lib", "/tmp/metadata-defer-lib")
    series = Series(name="Metadata Defer Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        filename="defer.cbz",
        library_root_id=library.active_root.id,
        relative_path="defer.cbz",
        metadata_json=json.dumps({"story_arc": "Arc Delta"}),
    )
    db.add_all([series, volume, comic])
    db.commit()
    comic_id = comic.id
    db.expunge_all()

    loaded = db.query(Comic).filter(Comic.id == comic_id).one()

    assert "metadata_json" in inspect(loaded).unloaded
    assert json.loads(loaded.metadata_json) == {"story_arc": "Arc Delta"}


def test_strip_raw_xml_migration_only_removes_raw_xml(db):
    library = create_library_with_root(db, "metadata-strip-lib", "/tmp/metadata-strip-lib")
    root = library.active_root
    series = Series(name="Metadata Strip Series", library=library)
    volume = Volume(series=series, volume_number=1)
    raw_values = {
        "with_xml": json.dumps({"story_arc": "Arc Epsilon", "raw_xml": "<ComicInfo/>"}),
        "without_xml": json.dumps({"story_arc": "Arc Zeta"}),
        "invalid": '{"raw_xml": broken',
        "missing": None,
    }
    comics = {
        key: Comic(
            volume=volume,
            number=str(index),
            filename=f"{key}.cbz",
            library_root_id=root.id,
            relative_path=f"{key}.cbz",
            metadata_json=value,
        )
        for index, (key, value) in enumerate(raw_values.items(), start=1)
    }
    db.add_all([series, volume, *comics.values()])
    db.commit()

    migration = _load_strip_raw_xml_migration()
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.upgrade()
    db.commit()

    stored = {
        key: db.execute(text("SELECT metadata_json FROM comics WHERE id = :id"), {"id": comic.id}).scalar()
        for key, comic in comics.items()
    }
    assert json.loads(stored["with_xml"]) == {"story_arc": "Arc Epsilon"}
    assert stored["without_xml"] == raw_values["without_xml"]
    assert stored["invalid"] == raw_values["invalid"]
    assert stored["missing"] is None


def test_rehydrate_library_metadata_from_cache_restores_expected_metadata(db):
    library = create_library_with_root(db, "metadata-rehydrate-lib", "/tmp/metadata-rehydrate-lib")
    root = library.active_root
    series = Series(name="Metadata Rehydrate Series", library=library)
    volume = Volume(series=series, volume_number=1)

    restorable = Comic(
        volume=volume,
        number="1",
        title="Restorable Issue",
        filename="restorable.cbz",
        library_root_id=root.id,
        relative_path="restorable.cbz",
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
        library_root_id=root.id,
        relative_path="missing-source.cbz",
        metadata_json=None,
    )

    db.add_all([series, volume, restorable, missing_source])
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
