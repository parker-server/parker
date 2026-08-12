import json
import zipfile
from xml.etree import ElementTree as ET

from app.models.comic import Comic, Volume
from app.models.collection import Collection, CollectionItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.services.metadata import MetadataService, parse_comicinfo, rehydrate_library_metadata_from_cache
from tests.factories import create_library_with_root


def _write_cbz(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _comicinfo_text(path, tag_name):
    with zipfile.ZipFile(path, "r") as archive:
        root = ET.fromstring(archive.read("ComicInfo.xml"))

    node = root.find(tag_name)
    return node.text if node is not None else None


def test_parse_comicinfo_reads_editable_metadata_fields():
    metadata = parse_comicinfo(
        b"""<?xml version="1.0" encoding="utf-8"?>
        <ComicInfo>
            <Series>Sandman</Series>
            <Number>8</Number>
            <Volume>1989</Volume>
            <Title>The Sound of Her Wings</Title>
            <Summary>Dream meets his sister.</Summary>
            <Year>1989</Year>
            <Writer>Neil Gaiman</Writer>
            <Penciller>Mike Dringenberg</Penciller>
            <Publisher>DC Comics</Publisher>
            <Imprint>Vertigo</Imprint>
            <Count>75</Count>
            <AlternateSeries>Death Reading Order</AlternateSeries>
            <AlternateNumber>12</AlternateNumber>
            <Genre>Fantasy, Horror</Genre>
            <Format>Special</Format>
            <AgeRating>Teen</AgeRating>
            <CommunityRating>6.5</CommunityRating>
        </ComicInfo>"""
    )

    assert metadata["series"] == "Sandman"
    assert metadata["number"] == "8"
    assert metadata["volume"] == "1989"
    assert metadata["title"] == "The Sound of Her Wings"
    assert metadata["summary"] == "Dream meets his sister."
    assert metadata["year"] == "1989"
    assert metadata["writer"] == "Neil Gaiman"
    assert metadata["penciller"] == "Mike Dringenberg"
    assert metadata["publisher"] == "DC Comics"
    assert metadata["imprint"] == "Vertigo"
    assert metadata["count"] == "75"
    assert metadata["alternate_series"] == "Death Reading Order"
    assert metadata["alternate_number"] == "12"
    assert metadata["genre"] == "Fantasy, Horror"
    assert metadata["format"] == "Special"
    assert metadata["age_rating"] == "Teen"
    assert metadata["community_rating"] == 5.0


def test_metadata_service_can_write_cbz_and_rejects_unwritable_or_unsupported_paths(tmp_path, monkeypatch):
    service = MetadataService()
    cbz_path = tmp_path / "writable.cbz"
    text_path = tmp_path / "notes.txt"
    cbr_path = tmp_path / "archive.cbr"

    cbz_path.write_bytes(b"zip bytes are not inspected for capability checks")
    text_path.write_bytes(b"notes")
    cbr_path.write_bytes(b"rar")
    service.rar_exe = None

    assert service.can_write(str(cbz_path)) is True
    assert service.can_write(str(text_path)) is False
    assert service.can_write(str(cbr_path)) is False
    assert service.can_write(str(tmp_path / "missing.cbz")) is False

    monkeypatch.setattr(
        "app.services.metadata.os.access",
        lambda path, _mode: str(path) != str(cbz_path.parent),
    )

    assert service.can_write(str(cbz_path)) is False

    monkeypatch.setattr("app.services.metadata.os.access", lambda _path, _mode: False)

    assert service.can_write(str(cbz_path)) is False


def test_metadata_service_read_metadata_parses_comicinfo_from_cbz(tmp_path):
    cbz_path = tmp_path / "read-metadata.cbz"
    _write_cbz(
        cbz_path,
        {
            "001.jpg": b"page-one",
            "ComicInfo.xml": b"""<ComicInfo>
                <Series>Read Series</Series>
                <Title>Read Title</Title>
                <Volume>3</Volume>
                <Imprint>Read Imprint</Imprint>
                <Count>6</Count>
                <AlternateSeries>Read Event</AlternateSeries>
                <AlternateNumber>2</AlternateNumber>
                <Genre>Adventure</Genre>
                <Format>TPB</Format>
                <AgeRating>Everyone</AgeRating>
            </ComicInfo>""",
        },
    )

    metadata = MetadataService().read_metadata(str(cbz_path))

    assert metadata["series"] == "Read Series"
    assert metadata["title"] == "Read Title"
    assert metadata["volume"] == "3"
    assert metadata["imprint"] == "Read Imprint"
    assert metadata["count"] == "6"
    assert metadata["alternate_series"] == "Read Event"
    assert metadata["alternate_number"] == "2"
    assert metadata["genre"] == "Adventure"
    assert metadata["format"] == "TPB"
    assert metadata["age_rating"] == "Everyone"


def test_metadata_service_write_metadata_updates_cbz_and_preserves_existing_pages(tmp_path):
    cbz_path = tmp_path / "update-metadata.cbz"
    _write_cbz(
        cbz_path,
        {
            "pages/001.jpg": b"page-one",
            "pages/002.jpg": b"page-two",
            "ComicInfo.xml": b"""<ComicInfo>
                <Series>Original Series</Series>
                <Title>Original Title</Title>
                <Summary>Remove me</Summary>
                <Imprint>Original Imprint</Imprint>
                <Count>4</Count>
            </ComicInfo>""",
        },
    )

    MetadataService().write_metadata(
        str(cbz_path),
        {
            "Title": "Updated Title",
            "summary": "",
            "Volume": "2",
            "Imprint": "Vertigo",
            "Count": "12",
            "AlternateSeries": "Updated Event",
            "AlternateNumber": "7",
            "Genre": "Superhero, Horror",
            "Format": "Annual",
            "AgeRating": "Mature 17+",
        },
    )

    with zipfile.ZipFile(cbz_path, "r") as archive:
        names = archive.namelist()
        assert names.count("ComicInfo.xml") == 1
        assert archive.read("pages/001.jpg") == b"page-one"
        assert archive.read("pages/002.jpg") == b"page-two"

    assert _comicinfo_text(cbz_path, "Series") == "Original Series"
    assert _comicinfo_text(cbz_path, "Title") == "Updated Title"
    assert _comicinfo_text(cbz_path, "Summary") is None
    assert _comicinfo_text(cbz_path, "Volume") == "2"
    assert _comicinfo_text(cbz_path, "Imprint") == "Vertigo"
    assert _comicinfo_text(cbz_path, "Count") == "12"
    assert _comicinfo_text(cbz_path, "AlternateSeries") == "Updated Event"
    assert _comicinfo_text(cbz_path, "AlternateNumber") == "7"
    assert _comicinfo_text(cbz_path, "Genre") == "Superhero, Horror"
    assert _comicinfo_text(cbz_path, "Format") == "Annual"
    assert _comicinfo_text(cbz_path, "AgeRating") == "Mature 17+"


def test_metadata_service_write_metadata_creates_comicinfo_when_missing(tmp_path):
    cbz_path = tmp_path / "create-metadata.cbz"
    _write_cbz(
        cbz_path,
        {
            "001.jpg": b"page-one",
        },
    )

    MetadataService().write_metadata(
        str(cbz_path),
        {
            "Series": "Created Series",
            "Number": "1",
            "Publisher": "Created Publisher",
            "Format": "Graphic Novel",
            "Genre": "Fantasy",
            "AgeRating": "Everyone 10+",
        },
    )

    with zipfile.ZipFile(cbz_path, "r") as archive:
        assert archive.read("001.jpg") == b"page-one"
        assert "ComicInfo.xml" in archive.namelist()

    assert _comicinfo_text(cbz_path, "Series") == "Created Series"
    assert _comicinfo_text(cbz_path, "Number") == "1"
    assert _comicinfo_text(cbz_path, "Publisher") == "Created Publisher"
    assert _comicinfo_text(cbz_path, "Format") == "Graphic Novel"
    assert _comicinfo_text(cbz_path, "Genre") == "Fantasy"
    assert _comicinfo_text(cbz_path, "AgeRating") == "Everyone 10+"


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
