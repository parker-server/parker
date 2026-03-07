from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series


def _create_volume_fixture(db, *, lib_name: str, series_name: str):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)

    db.add_all([library, series, volume])
    db.flush()

    comics = [
        Comic(
            volume_id=volume.id,
            number="10",
            title=f"{series_name} #10",
            year=2024,
            filename=f"{series_name}-10.cbz",
            file_path=f"/tmp/{series_name}-10-{lib_name}.cbz",
            page_count=20,
        ),
        Comic(
            volume_id=volume.id,
            number="2",
            title=f"{series_name} #2",
            year=2023,
            filename=f"{series_name}-2.cbz",
            file_path=f"/tmp/{series_name}-2-{lib_name}.cbz",
            page_count=22,
        ),
        Comic(
            volume_id=volume.id,
            number="1",
            title=f"{series_name} Annual",
            format="annual",
            year=2022,
            filename=f"{series_name}-annual.cbz",
            file_path=f"/tmp/{series_name}-annual-{lib_name}.cbz",
            page_count=30,
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


def test_volume_detail_blocks_restricted_content(auth_client, db, normal_user):
    data = _create_volume_fixture(db, lib_name="restricted-lib", series_name="Restricted Volume")

    mature = Comic(
        volume_id=data["volume"].id,
        number="11",
        title="Restricted Issue",
        age_rating="Mature 17+",
        filename="restricted.cbz",
        file_path="/tmp/restricted-issue.cbz",
    )
    db.add(mature)

    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    response = auth_client.get(f"/api/volumes/{data['volume'].id}")

    assert response.status_code == 403
    assert "restricted" in response.json()["detail"].lower()


def test_volume_issues_filters_sorting_and_read_status(auth_client, db, normal_user):
    data = _create_volume_fixture(db, lib_name="issues-lib", series_name="Volume Issues")
    normal_user.accessible_libraries.append(data["library"])

    read_progress = ReadingProgress(
        user_id=normal_user.id,
        comic_id=data["comics"][1].id,
        current_page=22,
        total_pages=22,
        completed=True,
    )
    db.add(read_progress)
    db.commit()

    response = auth_client.get(
        f"/api/volumes/{data['volume'].id}/issues?type=plain&sort_order=asc"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["number"] for item in payload["items"]] == ["2", "10"]

    read_response = auth_client.get(
        f"/api/volumes/{data['volume'].id}/issues?type=plain&read_filter=read"
    )

    assert read_response.status_code == 200
    read_payload = read_response.json()
    assert read_payload["total"] == 1
    assert read_payload["items"][0]["id"] == data["comics"][1].id
    assert read_payload["items"][0]["read"] is True
