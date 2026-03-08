from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series


def _seed_batch_graph(db, *, prefix: str):
    library = Library(name=f"{prefix}-lib", path=f"/tmp/{prefix}-lib")
    series_a = Series(name=f"{prefix}-series-a", library=library)
    series_b = Series(name=f"{prefix}-series-b", library=library)

    vol_a1 = Volume(series=series_a, volume_number=1)
    vol_a2 = Volume(series=series_a, volume_number=2)
    vol_b1 = Volume(series=series_b, volume_number=1)

    db.add_all([library, series_a, series_b, vol_a1, vol_a2, vol_b1])
    db.flush()

    c1 = Comic(
        volume_id=vol_a1.id,
        number="1",
        title=f"{prefix}-a1-1",
        filename=f"{prefix}-a1-1.cbz",
        file_path=f"/tmp/{prefix}-a1-1.cbz",
        page_count=10,
    )
    c2 = Comic(
        volume_id=vol_a1.id,
        number="2",
        title=f"{prefix}-a1-2",
        filename=f"{prefix}-a1-2.cbz",
        file_path=f"/tmp/{prefix}-a1-2.cbz",
        page_count=0,
    )
    c3 = Comic(
        volume_id=vol_a2.id,
        number="1",
        title=f"{prefix}-a2-1",
        filename=f"{prefix}-a2-1.cbz",
        file_path=f"/tmp/{prefix}-a2-1.cbz",
        page_count=5,
    )
    c4 = Comic(
        volume_id=vol_b1.id,
        number="1",
        title=f"{prefix}-b1-1",
        filename=f"{prefix}-b1-1.cbz",
        file_path=f"/tmp/{prefix}-b1-1.cbz",
        page_count=7,
    )

    db.add_all([c1, c2, c3, c4])
    db.commit()

    for obj in (library, series_a, series_b, vol_a1, vol_a2, vol_b1, c1, c2, c3, c4):
        db.refresh(obj)

    return {
        "library": library,
        "series_a": series_a,
        "series_b": series_b,
        "vol_a1": vol_a1,
        "vol_a2": vol_a2,
        "vol_b1": vol_b1,
        "c1": c1,
        "c2": c2,
        "c3": c3,
        "c4": c4,
    }


def test_batch_mark_read_returns_no_items_when_empty(auth_client):
    response = auth_client.post("/api/batch/read-status", json={"comic_ids": [], "read": True})

    assert response.status_code == 200
    assert response.json() == {"message": "No items selected"}


def test_batch_mark_read_upserts_from_comic_ids(auth_client, db, normal_user):
    data = _seed_batch_graph(db, prefix="batch-upsert")

    existing = ReadingProgress(
        user_id=normal_user.id,
        comic_id=data["c1"].id,
        current_page=1,
        total_pages=10,
        completed=False,
    )
    db.add(existing)
    db.commit()

    response = auth_client.post(
        "/api/batch/read-status",
        json={"comic_ids": [data["c1"].id, data["c2"].id], "read": True},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Marked 2 comics as read"}

    c1_progress = db.query(ReadingProgress).filter(
        ReadingProgress.user_id == normal_user.id,
        ReadingProgress.comic_id == data["c1"].id,
    ).first()
    c2_progress = db.query(ReadingProgress).filter(
        ReadingProgress.user_id == normal_user.id,
        ReadingProgress.comic_id == data["c2"].id,
    ).first()

    assert c1_progress is not None
    assert c1_progress.completed is True
    assert c1_progress.total_pages == 10
    assert c1_progress.current_page == 9

    assert c2_progress is not None
    assert c2_progress.completed is True
    assert c2_progress.total_pages == 0
    assert c2_progress.current_page == 0


def test_batch_mark_read_expands_volume_ids(auth_client, db, normal_user):
    data = _seed_batch_graph(db, prefix="batch-volume")

    response = auth_client.post(
        "/api/batch/read-status",
        json={"volume_ids": [data["vol_a1"].id], "read": True},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Marked 2 comics as read"}

    rows = db.query(ReadingProgress).filter(ReadingProgress.user_id == normal_user.id).all()
    ids = {row.comic_id for row in rows}
    assert ids == {data["c1"].id, data["c2"].id}


def test_batch_mark_read_expands_series_ids(auth_client, db, normal_user):
    data = _seed_batch_graph(db, prefix="batch-series")

    response = auth_client.post(
        "/api/batch/read-status",
        json={"series_ids": [data["series_a"].id], "read": True},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Marked 3 comics as read"}

    rows = db.query(ReadingProgress).filter(ReadingProgress.user_id == normal_user.id).all()
    ids = {row.comic_id for row in rows}
    assert ids == {data["c1"].id, data["c2"].id, data["c3"].id}


def test_batch_mark_unread_deletes_progress_for_target_ids(auth_client, db, normal_user):
    data = _seed_batch_graph(db, prefix="batch-unread")

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["c1"].id,
            current_page=3,
            total_pages=10,
            completed=False,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["c2"].id,
            current_page=0,
            total_pages=0,
            completed=True,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["c4"].id,
            current_page=1,
            total_pages=7,
            completed=False,
        ),
    ])
    db.commit()

    response = auth_client.post(
        "/api/batch/read-status",
        json={"comic_ids": [data["c1"].id, data["c2"].id], "read": False},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Marked 2 comics as unread"}

    remaining = db.query(ReadingProgress).filter(ReadingProgress.user_id == normal_user.id).all()
    remaining_ids = {row.comic_id for row in remaining}
    assert remaining_ids == {data["c4"].id}

def test_batch_mark_read_skips_unknown_comic_ids(auth_client, db, normal_user):
    data = _seed_batch_graph(db, prefix="batch-unknown")

    response = auth_client.post(
        "/api/batch/read-status",
        json={"comic_ids": [data["c3"].id, 999999], "read": True},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Marked 2 comics as read"}

    rows = db.query(ReadingProgress).filter(ReadingProgress.user_id == normal_user.id).all()
    assert len(rows) == 1
    assert rows[0].comic_id == data["c3"].id
