from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series


def _seed_progress_data(db, *, lib_name: str, series_name: str):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)

    db.add_all([library, series, volume])
    db.flush()

    first = Comic(
        volume_id=volume.id,
        number="1",
        title=f"{series_name} #1",
        year=2024,
        filename=f"{series_name}-1.cbz",
        file_path=f"/tmp/{series_name}-1-{lib_name}.cbz",
        page_count=10,
    )
    second = Comic(
        volume_id=volume.id,
        number="2",
        title=f"{series_name} #2",
        year=2025,
        filename=f"{series_name}-2.cbz",
        file_path=f"/tmp/{series_name}-2-{lib_name}.cbz",
        page_count=12,
    )

    db.add_all([first, second])
    db.commit()

    for obj in (library, series, volume, first, second):
        db.refresh(obj)

    return {
        "library": library,
        "series": series,
        "volume": volume,
        "first": first,
        "second": second,
    }


def test_get_comic_progress_returns_empty_when_missing(auth_client, db):
    data = _seed_progress_data(db, lib_name="progress-empty", series_name="Empty Progress")

    response = auth_client.get(f"/api/progress/{data['first'].id}")

    assert response.status_code == 200
    assert response.json() == {"comic_id": data["first"].id, "has_progress": False}


def test_update_comic_progress_success(auth_client, db):
    data = _seed_progress_data(db, lib_name="progress-update", series_name="Update Progress")

    response = auth_client.post(
        f"/api/progress/{data['first'].id}",
        json={"current_page": 9, "total_pages": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["comic_id"] == data["first"].id
    assert payload["completed"] is True
    assert payload["progress_percentage"] == 100.0


def test_update_comic_progress_missing_comic_returns_404(auth_client):
    response = auth_client.post("/api/progress/99999", json={"current_page": 1})

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_mark_read_then_unread_flow(auth_client, db):
    data = _seed_progress_data(db, lib_name="progress-read", series_name="Read Flow")

    mark_read = auth_client.post(f"/api/progress/{data['second'].id}/mark-read")
    assert mark_read.status_code == 200
    assert mark_read.json() == {"comic_id": data["second"].id, "completed": True, "message": "Comic marked as read"}

    get_progress = auth_client.get(f"/api/progress/{data['second'].id}")
    assert get_progress.status_code == 200
    assert get_progress.json()["has_progress"] is True
    assert get_progress.json()["completed"] is True

    mark_unread = auth_client.delete(f"/api/progress/{data['second'].id}")
    assert mark_unread.status_code == 200
    assert mark_unread.json() == {"comic_id": data["second"].id, "message": "Comic marked as unread"}

    get_progress_after = auth_client.get(f"/api/progress/{data['second'].id}")
    assert get_progress_after.status_code == 200
    assert get_progress_after.json() == {"comic_id": data["second"].id, "has_progress": False}


def test_recent_progress_filters_completed_and_in_progress(auth_client, db, normal_user):
    data = _seed_progress_data(db, lib_name="progress-filter", series_name="Filter Progress")

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["first"].id,
            current_page=9,
            total_pages=10,
            completed=True,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["second"].id,
            current_page=4,
            total_pages=12,
            completed=False,
        ),
    ])
    db.commit()

    completed_response = auth_client.get("/api/progress/?filter=completed")
    assert completed_response.status_code == 200
    completed_payload = completed_response.json()
    assert completed_payload["filter"] == "completed"
    assert completed_payload["total"] == 1
    assert completed_payload["results"][0]["comic_id"] == data["first"].id

    in_progress_response = auth_client.get("/api/progress/?filter=in_progress")
    assert in_progress_response.status_code == 200
    in_progress_payload = in_progress_response.json()
    assert in_progress_payload["filter"] == "in_progress"
    assert in_progress_payload["total"] == 1
    assert in_progress_payload["results"][0]["comic_id"] == data["second"].id


def test_on_deck_only_returns_started_unfinished_items(auth_client, db, normal_user):
    data = _seed_progress_data(db, lib_name="progress-deck", series_name="Deck Progress")

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["first"].id,
            current_page=3,
            total_pages=10,
            completed=False,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["second"].id,
            current_page=0,
            total_pages=12,
            completed=False,
        ),
    ])
    db.commit()

    response = auth_client.get("/api/progress/on-deck?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["comic_id"] == data["first"].id
