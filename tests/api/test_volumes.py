from datetime import datetime, timezone

from app.models.comic import Volume
from app.models.credits import ComicCredit, Person
from app.models.interactions import UserVolumeFollow
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Character, Location, Team
from tests.factories import create_comic, create_library_with_root


def _create_volume_fixture(db, *, lib_name: str, series_name: str):
    library = create_library_with_root(db, lib_name, f"/tmp/{lib_name}")
    root = library.active_root
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)

    db.add_all([series, volume])
    db.flush()

    comics = [
        create_comic(
            db, volume, root, f"{series_name}-10.cbz",
            number="10",
            title=f"{series_name} #10",
            year=2024,
            filename=f"{series_name}-10.cbz",
            page_count=20,
        ),
        create_comic(
            db, volume, root, f"{series_name}-2.cbz",
            number="2",
            title=f"{series_name} #2",
            year=2023,
            filename=f"{series_name}-2.cbz",
            page_count=22,
        ),
        create_comic(
            db, volume, root, f"{series_name}-annual.cbz",
            number="1",
            title=f"{series_name} Annual",
            format="annual",
            year=2022,
            filename=f"{series_name}-annual.cbz",
            page_count=30,
        ),
    ]

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

    create_comic(
        db, data["volume"], data["library"].active_root, "restricted.cbz",
        number="11",
        title="Restricted Issue",
        age_rating="Mature 17+",
        filename="restricted.cbz",
    )

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


def test_volume_detail_reports_missing_zero_index_and_metadata(auth_client, db, normal_user):
    library = create_library_with_root(db, "vol-detail-lib", "/tmp/vol-detail-lib")
    root = library.active_root
    series = Series(name="Volume Detail Saga", library=library)
    volume = Volume(series=series, volume_number=1, summary_override="Volume override summary")
    db.add_all([series, volume])
    db.flush()

    issue_zero = create_comic(
        db, volume, root, "issue-0.cbz",
        number="0",
        title="Issue Zero",
        year=2000,
        count=4,
        story_arc="Alpha Arc",
        page_count=18,
        file_size=80,
        publisher="Detail Pub",
        imprint="Detail Imprint",
        filename="issue-0.cbz",
    )
    issue_one = create_comic(
        db, volume, root, "issue-1.cbz",
        number="1",
        title="Issue One",
        year=2001,
        summary="Issue one summary",
        count=4,
        story_arc="Alpha Arc",
        page_count=20,
        file_size=100,
        color_primary="#111111",
        color_secondary="#222222",
        publisher="Detail Pub",
        imprint="Detail Imprint",
        filename="issue-1.cbz",
    )
    issue_three = create_comic(
        db, volume, root, "issue-3.cbz",
        number="3",
        title="Issue Three",
        year=2003,
        count=4,
        story_arc="Alpha Arc",
        page_count=22,
        file_size=120,
        publisher="Detail Pub",
        imprint="Detail Imprint",
        filename="issue-3.cbz",
    )
    annual = create_comic(
        db, volume, root, "annual.cbz",
        number="1",
        title="Annual",
        format="annual",
        year=2004,
        story_arc="Omega Arc",
        page_count=30,
        file_size=140,
        filename="annual.cbz",
    )
    special = create_comic(
        db, volume, root, "special.cbz",
        number="0.5",
        title="Special",
        format="one-shot",
        year=2005,
        story_arc="Omega Arc",
        page_count=26,
        file_size=90,
        filename="special.cbz",
    )

    writer = Person(name="Writer A")
    penciller = Person(name="Penciller A")
    hero = Character(name="Hero A")
    team = Team(name="Team A")
    location = Location(name="Location A")
    db.add_all([writer, penciller, hero, team, location])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=issue_one.id, person_id=writer.id, role="writer"),
        ComicCredit(comic_id=issue_three.id, person_id=writer.id, role="writer"),
        ComicCredit(comic_id=issue_one.id, person_id=penciller.id, role="penciller"),
    ])

    issue_one.characters.append(hero)
    issue_three.characters.append(hero)
    issue_one.teams.append(team)
    issue_one.locations.append(location)

    normal_user.accessible_libraries.append(library)
    db.flush()

    db.add(
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=issue_three.id,
            current_page=5,
            total_pages=22,
            completed=False,
            last_read_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    response = auth_client.get(f"/api/volumes/{volume.id}")

    assert response.status_code == 200
    payload = response.json()

    assert payload["id"] == volume.id
    assert payload["series_id"] == series.id
    assert payload["library_id"] == library.id
    assert payload["total_issues"] == 3
    assert payload["annual_count"] == 1
    assert payload["special_count"] == 1
    assert payload["expected_count"] == 4
    assert payload["status"] == "ended"
    assert payload["is_completed"] is False
    assert payload["is_standalone"] is False
    assert payload["missing_issues"] == [2]
    assert payload["first_issue_id"] == issue_one.id
    assert payload["first_issue_summary"] == "Volume override summary"
    assert payload["resume_to"] == {"comic_id": issue_three.id, "status": "in_progress"}
    assert payload["colors"] == {"primary": "#111111", "secondary": "#222222"}
    assert payload["details"]["writers"] == ["Writer A"]
    assert payload["details"]["pencillers"] == ["Penciller A"]
    assert payload["details"]["characters"] == ["Hero A"]
    assert payload["details"]["teams"] == ["Team A"]
    assert payload["details"]["locations"] == ["Location A"]
    assert [arc["name"] for arc in payload["story_arcs"]] == ["Alpha Arc", "Omega Arc"]
    assert payload["is_reverse_numbering"] is False


def test_volume_detail_hides_story_arcs_when_parsing_disabled(auth_client, db, normal_user):
    library = create_library_with_root(db, "vol-story-parse-off-lib", "/tmp/vol-story-parse-off-lib", parse_story_arcs=False)
    root = library.active_root
    series = Series(name="Volume Story Off", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    create_comic(
        db, volume, root, "story-off.cbz",
        number="1",
        title="Story Arc Off",
        story_arc="Hidden Arc",
        filename="story-off.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/volumes/{volume.id}")

    assert response.status_code == 200
    assert response.json()["story_arcs"] == []


def test_volume_detail_marks_standalone_without_plain_issues(auth_client, db, normal_user):
    library = create_library_with_root(db, "vol-standalone-lib", "/tmp/vol-standalone-lib")
    root = library.active_root
    series = Series(name="Standalone Volume", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    create_comic(
        db, volume, root, "standalone-annual.cbz",
        number="1",
        title="Standalone Annual",
        format="annual",
        year=2020,
        page_count=40,
        file_size=150,
        filename="standalone-annual.cbz",
    )
    create_comic(
        db, volume, root, "standalone-special.cbz",
        number="2",
        title="Standalone Special",
        format="one-shot",
        year=2021,
        page_count=35,
        file_size=130,
        filename="standalone-special.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/volumes/{volume.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_issues"] == 0
    assert payload["annual_count"] == 1
    assert payload["special_count"] == 1
    assert payload["is_standalone"] is True
    assert payload["status"] == "ended"
    assert payload["is_completed"] is True
    assert payload["missing_issues"] == []
    assert payload["expected_count"] is None


def test_volume_detail_advances_to_next_issue_when_latest_progress_is_completed(auth_client, db, normal_user):
    library = create_library_with_root(db, "volume-next-lib", "/tmp/volume-next-lib")
    root = library.active_root
    series = Series(name="Volume Next Logic", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    issue_one = create_comic(
        db, volume, root, "volume-next-1.cbz",
        number="1",
        title="Volume Next #1",
        filename="volume-next-1.cbz",
    )
    issue_two = create_comic(
        db, volume, root, "volume-next-2.cbz",
        number="2",
        title="Volume Next #2",
        filename="volume-next-2.cbz",
    )
    issue_three = create_comic(
        db, volume, root, "volume-next-3.cbz",
        number="3",
        title="Volume Next #3",
        filename="volume-next-3.cbz",
    )

    normal_user.accessible_libraries.append(library)
    db.flush()

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=issue_one.id,
            current_page=20,
            total_pages=20,
            completed=True,
            last_read_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=issue_two.id,
            current_page=22,
            total_pages=22,
            completed=True,
            last_read_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    ])
    db.commit()

    response = auth_client.get(f"/api/volumes/{volume.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["resume_to"] == {"comic_id": issue_three.id, "status": "continue"}


def test_volume_follow_toggle_and_detail_state(auth_client, db, normal_user):
    data = _create_volume_fixture(db, lib_name="follow-lib", series_name="Follow Volume")
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    detail_before = auth_client.get(f"/api/volumes/{data['volume'].id}")
    assert detail_before.status_code == 200
    assert detail_before.json()["is_following"] is False

    follow_response = auth_client.post(f"/api/volumes/{data['volume'].id}/follow")
    assert follow_response.status_code == 200
    assert follow_response.json() == {"following": True}

    follow = db.query(UserVolumeFollow).filter_by(
        user_id=normal_user.id,
        volume_id=data["volume"].id,
    ).first()
    assert follow is not None
    assert follow.followed_at is not None

    detail_after_follow = auth_client.get(f"/api/volumes/{data['volume'].id}")
    assert detail_after_follow.status_code == 200
    assert detail_after_follow.json()["is_following"] is True

    unfollow_response = auth_client.delete(f"/api/volumes/{data['volume'].id}/follow")
    assert unfollow_response.status_code == 200
    assert unfollow_response.json() == {"following": False}

    assert db.query(UserVolumeFollow).filter_by(
        user_id=normal_user.id,
        volume_id=data["volume"].id,
    ).first() is None

    detail_after_unfollow = auth_client.get(f"/api/volumes/{data['volume'].id}")
    assert detail_after_unfollow.status_code == 200
    assert detail_after_unfollow.json()["is_following"] is False


def test_following_list_reports_new_arrivals_and_filters_hidden_volumes(auth_client, db, normal_user):
    visible = _create_volume_fixture(db, lib_name="following-visible-lib", series_name="Visible Follow")
    hidden = _create_volume_fixture(db, lib_name="following-hidden-lib", series_name="Hidden Follow")

    normal_user.accessible_libraries.append(visible["library"])

    for comic in visible["comics"]:
        comic.created_at = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    for comic in hidden["comics"]:
        comic.created_at = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    db.flush()

    baseline = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    db.add_all([
        UserVolumeFollow(user_id=normal_user.id, volume_id=visible["volume"].id, followed_at=baseline),
        UserVolumeFollow(user_id=normal_user.id, volume_id=hidden["volume"].id, followed_at=baseline),
    ])
    db.flush()

    visible_root = visible["library"].active_root
    new_issue = create_comic(
        db, visible["volume"], visible_root, "visible-follow-11.cbz",
        number="11",
        title="Visible Follow #11",
        year=2026,
        created_at=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        filename="visible-follow-11.cbz",
    )
    started_issue = create_comic(
        db, visible["volume"], visible_root, "visible-follow-12.cbz",
        number="12",
        title="Visible Follow #12",
        year=2026,
        created_at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        filename="visible-follow-12.cbz",
    )
    annual = create_comic(
        db, visible["volume"], visible_root, "visible-follow-annual.cbz",
        number="1",
        title="Visible Follow Annual",
        format="annual",
        year=2026,
        created_at=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
        filename="visible-follow-annual.cbz",
    )

    db.add(
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=started_issue.id,
            current_page=1,
            total_pages=20,
            completed=False,
            last_read_at=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
        )
    )
    db.commit()

    response = auth_client.get("/api/volumes/following")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1

    item = payload[0]
    assert item["series_name"] == "Visible Follow"
    assert item["volume_id"] == visible["volume"].id
    assert item["new_arrivals_count"] == 1
    assert item["latest_arrival"]["comic_id"] == new_issue.id
    assert item["latest_arrival"]["title"] == "Visible Follow #11"
    assert item["latest_arrival"]["number"] == "11"


def test_volume_issues_returns_404_without_access(auth_client, db):
    data = _create_volume_fixture(db, lib_name="hidden-issues-lib", series_name="Hidden Issues")

    response = auth_client.get(f"/api/volumes/{data['volume'].id}/issues")

    assert response.status_code == 404
    assert response.json() == {"detail": "Volume not found"}


def test_volume_follow_returns_404_without_access(auth_client, db):
    data = _create_volume_fixture(db, lib_name="hidden-follow-lib", series_name="Hidden Follow")

    follow_response = auth_client.post(f"/api/volumes/{data['volume'].id}/follow")
    assert follow_response.status_code == 404
    assert follow_response.json() == {"detail": "Volume not found"}

    unfollow_response = auth_client.delete(f"/api/volumes/{data['volume'].id}/follow")
    assert unfollow_response.status_code == 404
    assert unfollow_response.json() == {"detail": "Volume not found"}


def test_volume_issues_annual_unread_desc_filter(auth_client, db, normal_user):
    data = _create_volume_fixture(db, lib_name="annual-issues-lib", series_name="Annual Issues")
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    response = auth_client.get(
        f"/api/volumes/{data['volume'].id}/issues?type=annual&read_filter=unread&sort_order=desc"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["format"] == "annual"
    assert payload["items"][0]["read"] is False
