from datetime import datetime, timezone

from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.interactions import UserSeries
from app.models.library import Library
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Character, Location, Team


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


def _create_series_detail_fixture(db):
    library = Library(name="series-detail-lib", path="/tmp/series-detail-lib")
    series = Series(name="Countdown", library=library, summary_override="Series override summary")
    vol1 = Volume(series=series, volume_number=1)
    vol2 = Volume(series=series, volume_number=2)
    db.add_all([library, series, vol1, vol2])
    db.flush()

    issue_one = Comic(
        volume_id=vol1.id,
        number="1",
        title="Countdown #1",
        year=2000,
        story_arc="Arc Prime",
        page_count=20,
        file_size=100,
        publisher="Series Pub",
        imprint="Series Imprint",
        filename="countdown-1.cbz",
        file_path="/tmp/series-countdown-1.cbz",
    )
    issue_four = Comic(
        volume_id=vol1.id,
        number="4",
        title="Countdown #4",
        year=2000,
        story_arc="Arc Prime",
        page_count=22,
        file_size=110,
        color_palette={"accent": "#123456"},
        publisher="Series Pub",
        imprint="Series Imprint",
        filename="countdown-4.cbz",
        file_path="/tmp/series-countdown-4.cbz",
    )
    issue_two = Comic(
        volume_id=vol2.id,
        number="2",
        title="Countdown #2",
        year=2000,
        story_arc="Arc Prime",
        page_count=18,
        file_size=90,
        publisher="Series Pub",
        imprint="Series Imprint",
        filename="countdown-2.cbz",
        file_path="/tmp/series-countdown-2.cbz",
    )
    annual = Comic(
        volume_id=vol2.id,
        number="1",
        title="Countdown Annual",
        year=2001,
        format="annual",
        story_arc="Arc Side",
        page_count=28,
        file_size=120,
        publisher="Series Pub",
        imprint="Series Imprint",
        filename="countdown-annual.cbz",
        file_path="/tmp/series-countdown-annual.cbz",
    )
    db.add_all([issue_one, issue_four, issue_two, annual])
    db.flush()

    writer = Person(name="Series Writer")
    penciller = Person(name="Series Penciller")
    hero = Character(name="Series Hero")
    team = Team(name="Series Team")
    location = Location(name="Series Location")
    db.add_all([writer, penciller, hero, team, location])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=issue_one.id, person_id=writer.id, role="writer"),
        ComicCredit(comic_id=issue_two.id, person_id=writer.id, role="writer"),
        ComicCredit(comic_id=issue_four.id, person_id=penciller.id, role="penciller"),
    ])

    issue_one.characters.append(hero)
    issue_two.characters.append(hero)
    issue_one.teams.append(team)
    issue_one.locations.append(location)

    collection = Collection(name="Series Detail Collection", description="Collection desc")
    reading_list = ReadingList(name="Series Detail Reading List", description="Reading list desc")
    db.add_all([collection, reading_list])
    db.flush()

    db.add_all([
        CollectionItem(collection_id=collection.id, comic_id=issue_one.id),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=issue_two.id, position=1),
    ])

    db.commit()

    return {
        "library": library,
        "series": series,
        "vol1": vol1,
        "vol2": vol2,
        "issue_one": issue_one,
        "issue_two": issue_two,
        "issue_four": issue_four,
        "annual": annual,
        "collection": collection,
        "reading_list": reading_list,
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


def test_series_issues_filters_annual_and_special(auth_client, db, normal_user):
    data = _create_series_with_volume(db, lib_name="issues-type-lib", series_name="Type Logic")
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    annual_response = auth_client.get(
        f"/api/series/{data['series'].id}/issues?type=annual&read_filter=all"
    )
    assert annual_response.status_code == 200
    annual_payload = annual_response.json()
    assert annual_payload["total"] == 1
    assert len(annual_payload["items"]) == 1
    assert annual_payload["items"][0]["id"] == data["comics"][1].id
    assert annual_payload["items"][0]["number"] == "2"
    assert annual_payload["items"][0]["read"] is False

    special_response = auth_client.get(
        f"/api/series/{data['series'].id}/issues?type=special&read_filter=all"
    )
    assert special_response.status_code == 200
    special_payload = special_response.json()
    assert special_payload["total"] == 1
    assert len(special_payload["items"]) == 1
    assert special_payload["items"][0]["id"] == data["comics"][2].id
    assert special_payload["items"][0]["number"] == "3"
    assert special_payload["items"][0]["read"] is False


def test_series_detail_returns_enriched_payload(auth_client, db, normal_user):
    data = _create_series_detail_fixture(db)

    normal_user.accessible_libraries.append(data["library"])
    db.flush()

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["issue_one"].id,
            current_page=20,
            total_pages=20,
            completed=True,
            last_read_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["issue_four"].id,
            current_page=22,
            total_pages=22,
            completed=True,
            last_read_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["issue_two"].id,
            current_page=5,
            total_pages=18,
            completed=False,
            last_read_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
        UserSeries(user_id=normal_user.id, series_id=data["series"].id, is_starred=True),
    ])
    db.commit()

    response = auth_client.get(f"/api/series/{data['series'].id}")

    assert response.status_code == 200
    payload = response.json()

    assert payload["id"] == data["series"].id
    assert payload["library_id"] == data["library"].id
    assert payload["volume_count"] == 2
    assert payload["total_issues"] == 3
    assert payload["annual_count"] == 1
    assert payload["special_count"] == 0
    assert payload["is_standalone"] is False
    assert payload["starred"] is True
    assert payload["is_admin"] is False
    assert payload["is_reverse_numbering"] is True
    assert payload["first_issue_id"] == data["issue_four"].id
    assert payload["first_issue_summary"] == "Series override summary"
    assert payload["colors"] == {"accent": "#123456"}
    assert payload["resume_to"] == {"comic_id": data["issue_two"].id, "status": "in_progress"}
    assert payload["details"]["writers"] == ["Series Writer"]
    assert payload["details"]["pencillers"] == ["Series Penciller"]
    assert payload["details"]["characters"] == ["Series Hero"]
    assert payload["details"]["teams"] == ["Series Team"]
    assert payload["details"]["locations"] == ["Series Location"]
    assert [arc["name"] for arc in payload["story_arcs"]] == ["Arc Prime", "Arc Side"]

    assert payload["collections"] == [
        {
            "id": data["collection"].id,
            "name": "Series Detail Collection",
            "description": "Collection desc",
        }
    ]
    assert payload["reading_lists"] == [
        {
            "id": data["reading_list"].id,
            "name": "Series Detail Reading List",
            "description": "Reading list desc",
        }
    ]

    by_vol = {row["volume_id"]: row for row in payload["volumes"]}
    assert by_vol[data["vol1"].id]["first_issue_id"] == data["issue_four"].id
    assert by_vol[data["vol1"].id]["read"] is True
    assert by_vol[data["vol2"].id]["first_issue_id"] == data["issue_two"].id
    assert by_vol[data["vol2"].id]["read"] is False


def test_series_detail_returns_empty_structure_when_no_volumes(auth_client, db, normal_user):
    library = Library(name="empty-series-lib", path="/tmp/empty-series-lib")
    series = Series(name="Empty Series", library=library)
    db.add_all([library, series])
    db.commit()

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{series.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == series.id
    assert payload["volume_count"] == 0
    assert payload["total_issues"] == 0
    assert payload["volumes"] == []
    assert payload["collections"] == []
    assert payload["reading_lists"] == []


def test_series_detail_blocks_age_restricted_content(auth_client, db, normal_user):
    library = Library(name="restricted-series-lib", path="/tmp/restricted-series-lib")
    series = Series(name="Restricted Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()

    db.add(
        Comic(
            volume_id=volume.id,
            number="1",
            title="Restricted Comic",
            age_rating="Mature 17+",
            filename="restricted-series.cbz",
            file_path="/tmp/restricted-series.cbz",
        )
    )

    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get(f"/api/series/{series.id}")

    assert response.status_code == 403
    assert response.json() == {"detail": "Content restricted by age rating"}


def test_series_list_can_sort_by_updated_desc(auth_client, db, normal_user):
    first = _create_series_with_volume(db, lib_name="updated-lib-1", series_name="Updated One")
    second = _create_series_with_volume(db, lib_name="updated-lib-2", series_name="Updated Two")

    normal_user.accessible_libraries.extend([first["library"], second["library"]])
    db.flush()

    first["series"].updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    second["series"].updated_at = datetime(2021, 1, 1, tzinfo=timezone.utc)
    db.commit()

    response = auth_client.get("/api/series/?sort_by=updated&sort_desc=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["id"] for item in payload["items"]] == [second["series"].id, first["series"].id]


def test_series_recommendations_returns_empty_when_series_missing(auth_client):
    response = auth_client.get("/api/series/999999/recommendations")

    assert response.status_code == 200
    assert response.json() == []


def test_series_recommendations_includes_group_lane(auth_client, db, normal_user):
    library = Library(name="rec-lib", path="/tmp/rec-lib")
    source = Series(name="Source Series", library=library)
    other = Series(name="Other Series", library=library)
    source_vol = Volume(series=source, volume_number=1)
    other_vol = Volume(series=other, volume_number=1)
    db.add_all([library, source, other, source_vol, other_vol])
    db.flush()

    db.add_all([
        Comic(
            volume_id=source_vol.id,
            number="1",
            title="Source #1",
            series_group="Shared Verse",
            filename="source-1.cbz",
            file_path="/tmp/source-1.cbz",
        ),
        Comic(
            volume_id=other_vol.id,
            number="1",
            title="Other #1",
            series_group="Shared Verse",
            filename="other-1.cbz",
            file_path="/tmp/other-1.cbz",
        ),
    ])

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{source.id}/recommendations?limit=10")

    assert response.status_code == 200
    lanes = response.json()
    assert len(lanes) >= 1
    assert any(lane["title"] == "More in 'Shared Verse'" for lane in lanes)
    shared_lane = next(lane for lane in lanes if lane["title"] == "More in 'Shared Verse'")
    assert len(shared_lane["items"]) == 1
    assert shared_lane["items"][0]["id"] == other.id



def _create_single_issue_series(
    db,
    library,
    *,
    name: str,
    number: str = "1",
    year: int = 2024,
    age_rating: str | None = None,
    series_group: str | None = None,
    publisher: str | None = None,
    genre=None,
    writer=None,
    penciller=None,
):
    series = Series(name=name, library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number=number,
        title=f"{name} #{number}",
        year=year,
        age_rating=age_rating,
        series_group=series_group,
        publisher=publisher,
        filename=f"{name}-{number}.cbz",
        file_path=f"/tmp/{name}-{number}.cbz",
    )

    db.add_all([series, volume, comic])
    db.flush()

    if genre is not None:
        comic.genres.append(genre)

    if writer is not None:
        db.add(ComicCredit(comic_id=comic.id, person_id=writer.id, role="writer"))

    if penciller is not None:
        db.add(ComicCredit(comic_id=comic.id, person_id=penciller.id, role="penciller"))

    db.flush()
    return {"series": series, "volume": volume, "comic": comic}


def test_series_list_fallback_cover_handles_non_numeric_issue_numbers(auth_client, db, normal_user):
    library = Library(name="series-fallback-list-lib", path="/tmp/series-fallback-list-lib")
    series = Series(name="Fallback Sort Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()

    comic_alpha = Comic(
        volume_id=volume.id,
        number="A",
        title="Fallback A",
        year=2025,
        filename="fallback-a.cbz",
        file_path="/tmp/fallback-a.cbz",
    )
    comic_two = Comic(
        volume_id=volume.id,
        number="2",
        title="Fallback 2",
        year=2024,
        filename="fallback-2.cbz",
        file_path="/tmp/fallback-2.cbz",
    )
    db.add_all([comic_alpha, comic_two])

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get("/api/series/")

    assert response.status_code == 200
    payload = response.json()
    item = next(x for x in payload["items"] if x["id"] == series.id)
    assert item["start_year"] == 2024
    assert str(comic_two.id) in item["thumbnail_path"]


def test_series_detail_non_reverse_cover_logic_and_resume_default(auth_client, db, normal_user):
    library = Library(name="series-detail-cover-lib", path="/tmp/series-detail-cover-lib")
    series = Series(name="Alpha Line", library=library)
    vol1 = Volume(series=series, volume_number=1)
    vol2 = Volume(series=series, volume_number=2)
    db.add_all([library, series, vol1, vol2])
    db.flush()

    v1_issue_alpha = Comic(
        volume_id=vol1.id,
        number="A",
        title="Vol1 A",
        year=2026,
        filename="v1-a.cbz",
        file_path="/tmp/v1-a.cbz",
    )
    v1_issue_two = Comic(
        volume_id=vol1.id,
        number="2",
        title="Vol1 #2",
        year=2025,
        filename="v1-2.cbz",
        file_path="/tmp/v1-2.cbz",
    )
    v2_issue_one = Comic(
        volume_id=vol2.id,
        number="1",
        title="Vol2 #1",
        year=2024,
        filename="v2-1.cbz",
        file_path="/tmp/v2-1.cbz",
    )
    v2_issue_three = Comic(
        volume_id=vol2.id,
        number="3",
        title="Vol2 #3",
        year=2027,
        filename="v2-3.cbz",
        file_path="/tmp/v2-3.cbz",
    )
    db.add_all([v1_issue_alpha, v1_issue_two, v2_issue_one, v2_issue_three])

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{series.id}")

    assert response.status_code == 200
    payload = response.json()
    by_vol = {row["volume_id"]: row for row in payload["volumes"]}

    assert by_vol[vol1.id]["first_issue_id"] == v1_issue_two.id
    assert by_vol[vol2.id]["first_issue_id"] == v2_issue_one.id
    assert payload["resume_to"]["status"] == "new"
    assert payload["resume_to"]["comic_id"] == payload["first_issue_id"]


def test_series_detail_filters_related_containers_with_banned_content(auth_client, db, normal_user):
    library = Library(name="series-detail-ban-lib", path="/tmp/series-detail-ban-lib")

    safe_series = Series(name="Safe Detail Series", library=library)
    safe_volume = Volume(series=safe_series, volume_number=1)
    safe_comic = Comic(
        volume=safe_volume,
        number="1",
        title="Safe Detail",
        age_rating="Teen",
        filename="safe-detail.cbz",
        file_path="/tmp/safe-detail.cbz",
    )

    banned_series = Series(name="Banned Detail Series", library=library)
    banned_volume = Volume(series=banned_series, volume_number=1)
    banned_comic = Comic(
        volume=banned_volume,
        number="1",
        title="Banned Detail",
        age_rating="Mature 17+",
        filename="banned-detail.cbz",
        file_path="/tmp/banned-detail.cbz",
    )

    collection = Collection(name="Mixed Collection")
    reading_list = ReadingList(name="Mixed Reading List")

    db.add_all([
        library,
        safe_series,
        safe_volume,
        safe_comic,
        banned_series,
        banned_volume,
        banned_comic,
        collection,
        reading_list,
    ])
    db.flush()

    db.add_all([
        CollectionItem(collection_id=collection.id, comic_id=safe_comic.id),
        CollectionItem(collection_id=collection.id, comic_id=banned_comic.id),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=safe_comic.id, position=1),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=banned_comic.id, position=2),
    ])

    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get(f"/api/series/{safe_series.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["collections"] == []
    assert payload["reading_lists"] == []


def test_series_list_applies_age_filter_and_created_sort(auth_client, db, normal_user):
    library = Library(name="series-list-age-lib", path="/tmp/series-list-age-lib")

    safe_series = Series(name="Safe Created Series", library=library)
    banned_series = Series(name="Banned Created Series", library=library)
    safe_volume = Volume(series=safe_series, volume_number=1)
    banned_volume = Volume(series=banned_series, volume_number=1)
    db.add_all([library, safe_series, banned_series, safe_volume, banned_volume])
    db.flush()

    db.add_all([
        Comic(
            volume_id=safe_volume.id,
            number="1",
            title="Safe Created",
            age_rating="Teen",
            filename="safe-created.cbz",
            file_path="/tmp/safe-created.cbz",
        ),
        Comic(
            volume_id=banned_volume.id,
            number="1",
            title="Banned Created",
            age_rating="Mature 17+",
            filename="banned-created.cbz",
            file_path="/tmp/banned-created.cbz",
        ),
    ])

    safe_series.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    banned_series.created_at = datetime(2021, 1, 1, tzinfo=timezone.utc)

    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/series/?sort_by=created&sort_desc=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == safe_series.id


def test_series_thumbnails_404_when_series_missing(admin_client):
    response = admin_client.post("/api/series/999999/thumbnails")

    assert response.status_code == 404


def test_series_thumbnails_background_task_handles_errors(admin_client, db, monkeypatch):
    library = Library(name="series-thumb-lib", path="/tmp/series-thumb-lib")
    series = Series(name="Series Thumbs", library=library)
    db.add_all([library, series])
    db.commit()

    called = {"value": False}

    def _raise_in_task(self, series_id):
        called["value"] = True
        raise RuntimeError("thumbnail failure")

    monkeypatch.setattr("app.api.series.ThumbnailService.process_series_thumbnails", _raise_in_task)

    response = admin_client.post(f"/api/series/{series.id}/thumbnails")

    assert response.status_code == 200
    assert response.json() == {"message": "Thumbnail regeneration started"}
    assert called["value"] is True


def test_series_issues_sort_order_none_uses_reverse_numbering_rule(db, normal_user):
    import asyncio
    from app.api.deps import PaginationParams
    from app.api.series import get_series_issues

    library = Library(name="series-issues-none-lib", path="/tmp/series-issues-none-lib")
    reverse_series = Series(name="Countdown", library=library)
    reverse_volume = Volume(series=reverse_series, volume_number=1)

    normal_series = Series(name="Regular Sort Series", library=library)
    normal_volume = Volume(series=normal_series, volume_number=1)

    db.add_all([library, reverse_series, reverse_volume, normal_series, normal_volume])
    db.flush()

    db.add_all([
        Comic(
            volume_id=reverse_volume.id,
            number="1",
            title="Reverse #1",
            filename="reverse-1.cbz",
            file_path="/tmp/reverse-1.cbz",
        ),
        Comic(
            volume_id=reverse_volume.id,
            number="2",
            title="Reverse #2",
            filename="reverse-2.cbz",
            file_path="/tmp/reverse-2.cbz",
        ),
        Comic(
            volume_id=normal_volume.id,
            number="1",
            title="Regular #1",
            filename="regular-1.cbz",
            file_path="/tmp/regular-1.cbz",
        ),
        Comic(
            volume_id=normal_volume.id,
            number="2",
            title="Regular #2",
            filename="regular-2.cbz",
            file_path="/tmp/regular-2.cbz",
        ),
    ])
    db.commit()

    params = PaginationParams(page=1, size=20)

    reverse_payload = asyncio.run(
        get_series_issues(
            current_user=normal_user,
            series_id=reverse_series.id,
            params=params,
            db=db,
            type="all",
            read_filter="all",
            sort_order=None,
        )
    )
    assert [row["number"] for row in reverse_payload["items"]] == ["2", "1"]

    normal_payload = asyncio.run(
        get_series_issues(
            current_user=normal_user,
            series_id=normal_series.id,
            params=params,
            db=db,
            type="all",
            read_filter="all",
            sort_order=None,
        )
    )
    assert [row["number"] for row in normal_payload["items"]] == ["1", "2"]


def test_series_recommendations_writer_lane(auth_client, db, normal_user):
    library = Library(name="series-rec-writer-lib", path="/tmp/series-rec-writer-lib")
    writer = Person(name="Writer Lane")
    db.add_all([library, writer])
    db.flush()

    source = _create_single_issue_series(db, library, name="Writer Source", writer=writer)
    match_ids = []
    for i in range(3):
        row = _create_single_issue_series(db, library, name=f"Writer Match {i}", writer=writer)
        match_ids.append(row["series"].id)

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{source['series'].id}/recommendations?limit=10")

    assert response.status_code == 200
    lanes = response.json()
    lane = next(x for x in lanes if x["title"] == "More by Writer Lane")
    ids = {item["id"] for item in lane["items"]}
    assert set(match_ids).issubset(ids)


def test_series_recommendations_penciller_lane(auth_client, db, normal_user):
    library = Library(name="series-rec-pencil-lib", path="/tmp/series-rec-pencil-lib")
    penciller = Person(name="Penciller Lane")
    db.add_all([library, penciller])
    db.flush()

    source = _create_single_issue_series(db, library, name="Penciller Source", penciller=penciller)
    match_ids = []
    for i in range(3):
        row = _create_single_issue_series(db, library, name=f"Penciller Match {i}", penciller=penciller)
        match_ids.append(row["series"].id)

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{source['series'].id}/recommendations?limit=10")

    assert response.status_code == 200
    lanes = response.json()
    lane = next(x for x in lanes if x["title"] == "More by Penciller Lane (Art)")
    ids = {item["id"] for item in lane["items"]}
    assert set(match_ids).issubset(ids)


def test_series_recommendations_genre_lane(auth_client, db, normal_user):
    from app.models.tags import Genre

    library = Library(name="series-rec-genre-lib", path="/tmp/series-rec-genre-lib")
    genre = Genre(name="Mystery")
    db.add_all([library, genre])
    db.flush()

    source = _create_single_issue_series(db, library, name="Genre Source", genre=genre)
    match_ids = []
    for i in range(5):
        row = _create_single_issue_series(db, library, name=f"Genre Match {i}", genre=genre)
        match_ids.append(row["series"].id)

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{source['series'].id}/recommendations?limit=10")

    assert response.status_code == 200
    lanes = response.json()
    lane = next(x for x in lanes if x["title"] == "More Mystery Comics")
    ids = {item["id"] for item in lane["items"]}
    assert set(match_ids).issubset(ids)


def test_series_recommendations_publisher_lane(auth_client, db, normal_user):
    library = Library(name="series-rec-publisher-lib", path="/tmp/series-rec-publisher-lib")
    source = _create_single_issue_series(db, library, name="Publisher Source", publisher="Pub Lane")
    match_ids = []
    for i in range(5):
        row = _create_single_issue_series(db, library, name=f"Publisher Match {i}", publisher="Pub Lane")
        match_ids.append(row["series"].id)

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/series/{source['series'].id}/recommendations?limit=10")

    assert response.status_code == 200
    lanes = response.json()
    lane = next(x for x in lanes if x["title"] == "More from Pub Lane")
    ids = {item["id"] for item in lane["items"]}
    assert set(match_ids).issubset(ids)


def test_series_recommendations_apply_age_filter_for_restricted_user(auth_client, db, normal_user):
    library = Library(name="series-rec-age-lib", path="/tmp/series-rec-age-lib")
    db.add(library)
    db.flush()

    source = _create_single_issue_series(
        db,
        library,
        name="Age Source",
        series_group="Shared Lane",
        age_rating="Teen",
    )
    safe_match = _create_single_issue_series(
        db,
        library,
        name="Age Safe Match",
        series_group="Shared Lane",
        age_rating="Teen",
    )
    banned_match = _create_single_issue_series(
        db,
        library,
        name="Age Banned Match",
        series_group="Shared Lane",
        age_rating="Mature 17+",
    )

    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get(f"/api/series/{source['series'].id}/recommendations?limit=10")

    assert response.status_code == 200
    lanes = response.json()
    lane = next(x for x in lanes if x["title"] == "More in 'Shared Lane'")
    lane_ids = [item["id"] for item in lane["items"]]

    assert safe_match["series"].id in lane_ids
    assert banned_match["series"].id not in lane_ids


def test_series_list_reverse_numbering_fallback_uses_highest_issue(auth_client, db, normal_user):
    library = Library(name="series-reverse-list-lib", path="/tmp/series-reverse-list-lib")
    series = Series(name="Countdown", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()

    issue_two = Comic(
        volume_id=volume.id,
        number="2",
        title="Countdown #2",
        year=2008,
        filename="countdown-2.cbz",
        file_path="/tmp/countdown-2.cbz",
    )
    issue_four = Comic(
        volume_id=volume.id,
        number="4",
        title="Countdown #4",
        year=2007,
        filename="countdown-4.cbz",
        file_path="/tmp/countdown-4.cbz",
    )
    db.add_all([issue_two, issue_four])

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get("/api/series/")

    assert response.status_code == 200
    payload = response.json()
    item = next(x for x in payload["items"] if x["id"] == series.id)
    assert item["start_year"] == 2007
    assert str(issue_four.id) in item["thumbnail_path"]
