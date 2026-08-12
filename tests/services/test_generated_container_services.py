from app.models import (
    CollectionItem,
    Comic,
    ReadingList,
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


def test_update_comic_reading_lists_skips_when_name_collides_with_manual_list(db):
    comic = _create_comic(db, "list-collision")
    manual_list = ReadingList(name="Crisis", source="manual")
    db.add(manual_list)
    db.commit()

    service = ReadingListService(db)
    service.update_comic_reading_lists(comic, "Crisis", "1")
    db.commit()

    # The comic gets no membership at all -- it must not be silently written
    # into the manual list, and no second "Crisis" list gets created either
    # (ComicInfo-derived names are never renamed/disambiguated).
    assert db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic.id).count() == 0
    lists = db.query(ReadingList).filter(ReadingList.name == "Crisis").all()
    assert len(lists) == 1
    assert lists[0].id == manual_list.id
    assert lists[0].source == "manual"


def test_update_comic_reading_lists_skips_repeated_collision_via_cache(db):
    comic_a = _create_comic(db, "list-collision-a")
    comic_b = _create_comic(db, "list-collision-b")
    db.add(ReadingList(name="Crisis", source="manual"))
    db.commit()

    service = ReadingListService(db)
    service.update_comic_reading_lists(comic_a, "Crisis", "1")
    service.update_comic_reading_lists(comic_b, "Crisis", "2")
    db.commit()

    assert db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic_a.id).count() == 0
    assert db.query(ReadingListItem).filter(ReadingListItem.comic_id == comic_b.id).count() == 0
    assert db.query(ReadingList).filter(ReadingList.name == "Crisis").count() == 1


def test_cleanup_empty_lists_only_removes_comicinfo_lists(db):
    manual_empty = ReadingList(name="Manual Empty", source="manual")
    cbl_empty = ReadingList(name="CBL Empty", source="cbl")
    comicinfo_empty = ReadingList(name="Comicinfo Empty", source="comicinfo")
    db.add_all([manual_empty, cbl_empty, comicinfo_empty])
    db.commit()

    service = ReadingListService(db)
    service.cleanup_empty_lists()
    db.commit()

    # CBL list lifecycle is owned by CBLSourceService.rebuild()/delete() --
    # deleting an empty one here (e.g. from a rehydrate pass that never
    # rebuilds CBL sources) would orphan its CBLSource until the next scan.
    assert db.query(ReadingList).filter(ReadingList.id == manual_empty.id).count() == 1
    assert db.query(ReadingList).filter(ReadingList.id == cbl_empty.id).count() == 1
    assert db.query(ReadingList).filter(ReadingList.id == comicinfo_empty.id).count() == 0
