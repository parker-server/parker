from app.core.security import get_password_hash
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.pull_list import PullList, PullListItem
from app.models.series import Series
from app.models.user import User


def _seed_comics(db, prefix="pull"):
    lib = Library(name=f"{prefix}-lib", path=f"/tmp/{prefix}-lib")
    series = Series(name=f"{prefix}-series", library=lib)
    volume = Volume(series=series, volume_number=1)

    db.add_all([lib, series, volume])
    db.flush()

    comics = [
        Comic(
            volume_id=volume.id,
            number="1",
            title=f"{prefix}-one",
            filename=f"{prefix}-1.cbz",
            file_path=f"/tmp/{prefix}-1.cbz",
        ),
        Comic(
            volume_id=volume.id,
            number="2",
            title=f"{prefix}-two",
            filename=f"{prefix}-2.cbz",
            file_path=f"/tmp/{prefix}-2.cbz",
        ),
        Comic(
            volume_id=volume.id,
            number="3",
            title=f"{prefix}-three",
            filename=f"{prefix}-3.cbz",
            file_path=f"/tmp/{prefix}-3.cbz",
        ),
    ]

    db.add_all(comics)
    db.commit()

    for c in comics:
        db.refresh(c)

    return comics


def test_pull_lists_list_create_and_owner_filter(auth_client, db, normal_user):
    other_user = User(
        username="otherpulluser",
        email="otherpull@example.com",
        hashed_password=get_password_hash("password123"),
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.commit()

    mine_b = PullList(user_id=normal_user.id, name="B List")
    mine_a = PullList(user_id=normal_user.id, name="A List")
    not_mine = PullList(user_id=other_user.id, name="Other List")
    db.add_all([mine_b, mine_a, not_mine])
    db.commit()

    response = auth_client.get("/api/pull-lists/")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["A List", "B List"]

    create = auth_client.post(
        "/api/pull-lists/",
        json={"name": "Created List", "description": "for reading"},
    )

    assert create.status_code == 200
    payload = create.json()
    assert payload["name"] == "Created List"
    assert payload["description"] == "for reading"


def test_pull_list_detail_returns_items_and_metadata(auth_client, db, normal_user):
    comics = _seed_comics(db, "detail")

    plist = PullList(user_id=normal_user.id, name="Detail List")
    db.add(plist)
    db.flush()

    db.add_all([
        PullListItem(pull_list_id=plist.id, comic_id=comics[1].id, sort_order=1),
        PullListItem(pull_list_id=plist.id, comic_id=comics[0].id, sort_order=0),
    ])
    db.commit()

    response = auth_client.get(f"/api/pull-lists/{plist.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Detail List"
    assert [item["id"] for item in payload["items"]] == [comics[0].id, comics[1].id]
    assert payload["details"] == {
        "writers": [],
        "pencillers": [],
        "characters": [],
        "teams": [],
        "locations": [],
    }

    missing = auth_client.get("/api/pull-lists/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Pull list not found"


def test_pull_list_update_and_delete_paths(auth_client, db, normal_user):
    plist = PullList(user_id=normal_user.id, name="Old", description="old desc")
    db.add(plist)
    db.commit()

    update = auth_client.put(
        f"/api/pull-lists/{plist.id}",
        json={"name": "New", "description": "new desc"},
    )

    assert update.status_code == 200
    assert update.json()["name"] == "New"
    assert update.json()["description"] == "new desc"

    update_missing = auth_client.put("/api/pull-lists/999999", json={"name": "x"})
    assert update_missing.status_code == 404

    delete = auth_client.delete(f"/api/pull-lists/{plist.id}")
    assert delete.status_code == 200
    assert delete.json() == {"message": "List deleted"}

    delete_missing = auth_client.delete(f"/api/pull-lists/{plist.id}")
    assert delete_missing.status_code == 404


def test_pull_list_add_item_paths(auth_client, db, normal_user):
    comics = _seed_comics(db, "add-item")
    plist = PullList(user_id=normal_user.id, name="Add Item")
    db.add(plist)
    db.commit()

    first = auth_client.post(f"/api/pull-lists/{plist.id}/items", json={"comic_id": comics[0].id})
    second = auth_client.post(f"/api/pull-lists/{plist.id}/items", json={"comic_id": comics[1].id})

    assert first.status_code == 200
    assert first.json()["sort_order"] == 0
    assert second.status_code == 200
    assert second.json()["sort_order"] == 1

    duplicate = auth_client.post(f"/api/pull-lists/{plist.id}/items", json={"comic_id": comics[0].id})
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Comic already in this list"

    comic_missing = auth_client.post(f"/api/pull-lists/{plist.id}/items", json={"comic_id": 999999})
    assert comic_missing.status_code == 404
    assert comic_missing.json()["detail"] == "Comic not found"

    list_missing = auth_client.post("/api/pull-lists/999999/items", json={"comic_id": comics[2].id})
    assert list_missing.status_code == 404
    assert list_missing.json()["detail"] == "Pull list not found"


def test_pull_list_remove_item_paths(auth_client, db, normal_user):
    comics = _seed_comics(db, "remove-item")
    plist = PullList(user_id=normal_user.id, name="Remove Item")
    db.add(plist)
    db.flush()
    db.add(PullListItem(pull_list_id=plist.id, comic_id=comics[0].id, sort_order=0))
    db.commit()

    removed = auth_client.delete(f"/api/pull-lists/{plist.id}/items/{comics[0].id}")
    assert removed.status_code == 200
    assert removed.json() == {"message": "Item removed"}

    missing_item = auth_client.delete(f"/api/pull-lists/{plist.id}/items/{comics[1].id}")
    assert missing_item.status_code == 404
    assert missing_item.json()["detail"] == "Item not found in list"

    missing_list = auth_client.delete(f"/api/pull-lists/999999/items/{comics[1].id}")
    assert missing_list.status_code == 404
    assert missing_list.json()["detail"] == "Pull list not found"


def test_pull_list_reorder_paths(auth_client, db, normal_user):
    comics = _seed_comics(db, "reorder")
    plist = PullList(user_id=normal_user.id, name="Reorder")
    db.add(plist)
    db.flush()
    db.add_all([
        PullListItem(pull_list_id=plist.id, comic_id=comics[0].id, sort_order=0),
        PullListItem(pull_list_id=plist.id, comic_id=comics[1].id, sort_order=1),
        PullListItem(pull_list_id=plist.id, comic_id=comics[2].id, sort_order=2),
    ])
    db.commit()

    reordered = auth_client.post(
        f"/api/pull-lists/{plist.id}/reorder",
        json={"comic_ids": [comics[2].id, comics[0].id, comics[1].id]},
    )

    assert reordered.status_code == 200

    items = db.query(PullListItem).filter(PullListItem.pull_list_id == plist.id).all()
    order_map = {i.comic_id: i.sort_order for i in items}
    assert order_map[comics[2].id] == 0
    assert order_map[comics[0].id] == 1
    assert order_map[comics[1].id] == 2

    missing_list = auth_client.post("/api/pull-lists/999999/reorder", json={"comic_ids": []})
    assert missing_list.status_code == 404
    assert missing_list.json()["detail"] == "Pull list not found"


def test_pull_list_batch_add_paths(auth_client, db, normal_user):
    comics = _seed_comics(db, "batch")
    plist = PullList(user_id=normal_user.id, name="Batch")
    db.add(plist)
    db.flush()
    db.add(PullListItem(pull_list_id=plist.id, comic_id=comics[0].id, sort_order=0))
    db.commit()

    empty = auth_client.post(f"/api/pull-lists/{plist.id}/items/batch", json={"comic_ids": []})
    assert empty.status_code == 200
    assert empty.json() == {"message": "No comics selected"}

    all_existing = auth_client.post(
        f"/api/pull-lists/{plist.id}/items/batch",
        json={"comic_ids": [comics[0].id]},
    )
    assert all_existing.status_code == 200
    assert all_existing.json() == {"message": "All selected comics are already in this list"}

    added = auth_client.post(
        f"/api/pull-lists/{plist.id}/items/batch",
        json={"comic_ids": [comics[0].id, comics[1].id, comics[2].id]},
    )
    assert added.status_code == 200
    assert added.json() == {"message": "Added 2 comics to list"}

    items = db.query(PullListItem).filter(PullListItem.pull_list_id == plist.id).order_by(PullListItem.sort_order).all()
    assert [i.comic_id for i in items] == [comics[0].id, comics[1].id, comics[2].id]
    assert [i.sort_order for i in items] == [0, 1, 2]

    missing_list = auth_client.post("/api/pull-lists/999999/items/batch", json={"comic_ids": [comics[2].id]})
    assert missing_list.status_code == 404
    assert missing_list.json()["detail"] == "Pull list not found"
