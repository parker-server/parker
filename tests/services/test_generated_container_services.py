from app.models import (
    CollectionItem,
    Comic,
    ReadingListItem,
    Series,
    Volume,
)
from app.services.collection import CollectionService
from app.services.reading_list import ReadingListService
from tests.factories import create_library_with_root


def _create_comic(db, suffix: str) -> Comic:
    library = create_library_with_root(db, f"Library {suffix}", f"/library/{suffix}")
    root = library.active_root
    series = Series(name=f"Series {suffix}", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        filename=f"{suffix}.cbz",
        library_root_id=root.id,
        relative_path=f"{suffix}.cbz",
        page_count=24,
    )
    db.add_all([series, volume, comic])
    db.commit()
    db.refresh(comic)
    return comic


def test_update_comic_collections_noops_when_membership_matches(db):
    comic = _create_comic(db, "collection-noop")
    service = CollectionService(db)

    service.update_comic_collections(comic, "Marvel Knights")
    db.commit()

    original_item = db.query(CollectionItem).filter(CollectionItem.comic_id == comic.id).one()

    service.update_comic_collections(comic, "Marvel Knights")
    db.commit()

    items = db.query(CollectionItem).filter(CollectionItem.comic_id == comic.id).all()
    assert len(items) == 1
    assert items[0].id == original_item.id
    assert items[0].collection.name == "Marvel Knights"


def test_update_comic_collections_reassigns_when_membership_changes(db):
    comic = _create_comic(db, "collection-reassign")
    service = CollectionService(db)

    service.update_comic_collections(comic, "Marvel Knights")
    db.commit()

    service.update_comic_collections(comic, "Street Level")
    db.commit()

    items = db.query(CollectionItem).filter(CollectionItem.comic_id == comic.id).all()
    assert len(items) == 1
    assert items[0].collection.name == "Street Level"


def test_update_comic_collections_removes_membership_when_group_clears(db):
    comic = _create_comic(db, "collection-clear")
    service = CollectionService(db)

    service.update_comic_collections(comic, "Marvel Knights")
    db.commit()

    service.update_comic_collections(comic, None)
    db.commit()

    assert db.query(CollectionItem).filter(CollectionItem.comic_id == comic.id).count() == 0


def test_update_comic_reading_lists_noops_when_name_and_position_match(db):
    comic = _create_comic(db, "list-noop")
    service = ReadingListService(db)

    service.update_comic_reading_lists(comic, "Civil War", "3")
    db.commit()

    original_item = db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).one()

    service.update_comic_reading_lists(comic, "Civil War", "3")
    db.commit()

    items = db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).all()
    assert len(items) == 1
    assert items[0].id == original_item.id
    assert items[0].reading_list.name == "Civil War"
    assert items[0].position == 3.0


def test_update_comic_reading_lists_updates_position_in_place(db):
    comic = _create_comic(db, "list-position")
    service = ReadingListService(db)

    service.update_comic_reading_lists(comic, "Civil War", "3")
    db.commit()

    original_item = db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).one()

    service.update_comic_reading_lists(comic, "Civil War", "4")
    db.commit()

    items = db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).all()
    assert len(items) == 1
    assert items[0].id == original_item.id
    assert items[0].position == 4.0
    assert items[0].reading_list.name == "Civil War"


def test_update_comic_reading_lists_reassigns_when_list_changes(db):
    comic = _create_comic(db, "list-reassign")
    service = ReadingListService(db)

    service.update_comic_reading_lists(comic, "Civil War", "3")
    db.commit()

    service.update_comic_reading_lists(comic, "Secret Invasion", "1")
    db.commit()

    items = db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).all()
    assert len(items) == 1
    assert items[0].reading_list.name == "Secret Invasion"
    assert items[0].position == 1.0


def test_update_comic_reading_lists_clears_membership_when_number_is_invalid(db):
    comic = _create_comic(db, "list-clear-invalid")
    service = ReadingListService(db)

    service.update_comic_reading_lists(comic, "Civil War", "3")
    db.commit()

    service.update_comic_reading_lists(comic, "Civil War", "not-a-number")
    db.commit()

    assert db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).count() == 0
