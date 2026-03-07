from app.models.comic import Comic, Volume
from app.models.interactions import UserSeries
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series


def _create_series_with_volume(db, *, lib_name: str, series_name: str):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)

    db.add_all([library, series, volume])
    db.flush()

    comics = [
        Comic(
            volume_id=volume.id,
            number="1",
            title=f"{series_name} #1",
            year=2020,
            filename=f"{series_name}-1.cbz",
            file_path=f"/tmp/{series_name}-1-{lib_name}.cbz",
            page_count=24,
        ),
        Comic(
            volume_id=volume.id,
            number="2",
            title=f"{series_name} Annual",
            year=2021,
            format="annual",
            filename=f"{series_name}-2.cbz",
            file_path=f"/tmp/{series_name}-2-{lib_name}.cbz",
            page_count=30,
        ),
        Comic(
            volume_id=volume.id,
            number="3",
            title=f"{series_name} Special",
            year=2022,
            format="one-shot",
            filename=f"{series_name}-3.cbz",
            file_path=f"/tmp/{series_name}-3-{lib_name}.cbz",
            page_count=20,
        ),
    ]

    db.add_all(comics)
    db.commit()

    db.refresh(library)
    db.refresh(series)
    db.refresh(volume)
    for comic in comics:
        db.refresh(comic)

    return {
        "library": library,
        "series": series,
        "volume": volume,
        "comics": comics,
    }


def test_series_star_and_unstar_flow(auth_client, db, normal_user):
    data = _create_series_with_volume(db, lib_name="star-lib", series_name="Star Saga")
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    response = auth_client.post(f"/api/series/{data['series'].id}/star")

    assert response.status_code == 200
    assert response.json() == {"starred": True}

    pref = db.query(UserSeries).filter_by(user_id=normal_user.id, series_id=data["series"].id).first()
    assert pref is not None
    assert pref.is_starred is True
    assert pref.starred_at is not None

    response = auth_client.delete(f"/api/series/{data['series'].id}/star")

    assert response.status_code == 200
    assert response.json() == {"starred": False}

    db.refresh(pref)
    assert pref.is_starred is False
    assert pref.starred_at is None


def test_series_list_only_starred_returns_starred_items(auth_client, db, normal_user):
    first = _create_series_with_volume(db, lib_name="list-lib-1", series_name="Alpha Line")
    second = _create_series_with_volume(db, lib_name="list-lib-2", series_name="Beta Line")

    normal_user.accessible_libraries.extend([first["library"], second["library"]])
    db.commit()

    star_response = auth_client.post(f"/api/series/{first['series'].id}/star")
    assert star_response.status_code == 200

    response = auth_client.get("/api/series/?only_starred=true")

    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == first["series"].id


def test_series_issues_filters_and_read_state(auth_client, db, normal_user):
    data = _create_series_with_volume(db, lib_name="issues-lib", series_name="Issue Logic")
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    read_progress = ReadingProgress(
        user_id=normal_user.id,
        comic_id=data["comics"][0].id,
        current_page=24,
        total_pages=24,
        completed=True,
    )
    db.add(read_progress)
    db.commit()

    read_response = auth_client.get(
        f"/api/series/{data['series'].id}/issues?type=plain&read_filter=read"
    )

    assert read_response.status_code == 200
    read_payload = read_response.json()

    assert read_payload["total"] == 1
    assert read_payload["items"][0]["id"] == data["comics"][0].id
    assert read_payload["items"][0]["read"] is True

    unread_desc = auth_client.get(
        f"/api/series/{data['series'].id}/issues?type=all&read_filter=unread&sort_order=desc"
    )

    assert unread_desc.status_code == 200
    unread_payload = unread_desc.json()

    assert unread_payload["total"] == 2
    assert [item["number"] for item in unread_payload["items"]] == ["3", "2"]
    assert all(item["read"] is False for item in unread_payload["items"])
