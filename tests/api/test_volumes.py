from datetime import datetime, timezone

from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.tags import Character, Location, Team


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


def test_volume_detail_reports_missing_zero_index_and_metadata(auth_client, db, normal_user):
    library = Library(name="vol-detail-lib", path="/tmp/vol-detail-lib")
    series = Series(name="Volume Detail Saga", library=library)
    volume = Volume(series=series, volume_number=1, summary_override="Volume override summary")
    db.add_all([library, series, volume])
    db.flush()

    issue_zero = Comic(
        volume_id=volume.id,
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
        file_path="/tmp/vol-detail-0.cbz",
    )
    issue_one = Comic(
        volume_id=volume.id,
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
        file_path="/tmp/vol-detail-1.cbz",
    )
    issue_three = Comic(
        volume_id=volume.id,
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
        file_path="/tmp/vol-detail-3.cbz",
    )
    annual = Comic(
        volume_id=volume.id,
        number="1",
        title="Annual",
        format="annual",
        year=2004,
        story_arc="Omega Arc",
        page_count=30,
        file_size=140,
        filename="annual.cbz",
        file_path="/tmp/vol-detail-annual.cbz",
    )
    special = Comic(
        volume_id=volume.id,
        number="0.5",
        title="Special",
        format="one-shot",
        year=2005,
        story_arc="Omega Arc",
        page_count=26,
        file_size=90,
        filename="special.cbz",
        file_path="/tmp/vol-detail-special.cbz",
    )
    db.add_all([issue_zero, issue_one, issue_three, annual, special])
    db.flush()

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


def test_volume_detail_marks_standalone_without_plain_issues(auth_client, db, normal_user):
    library = Library(name="vol-standalone-lib", path="/tmp/vol-standalone-lib")
    series = Series(name="Standalone Volume", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()

    annual = Comic(
        volume_id=volume.id,
        number="1",
        title="Standalone Annual",
        format="annual",
        year=2020,
        page_count=40,
        file_size=150,
        filename="standalone-annual.cbz",
        file_path="/tmp/standalone-annual.cbz",
    )
    special = Comic(
        volume_id=volume.id,
        number="2",
        title="Standalone Special",
        format="one-shot",
        year=2021,
        page_count=35,
        file_size=130,
        filename="standalone-special.cbz",
        file_path="/tmp/standalone-special.cbz",
    )
    db.add_all([annual, special])

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


def test_volume_issues_returns_404_without_access(auth_client, db):
    data = _create_volume_fixture(db, lib_name="hidden-issues-lib", series_name="Hidden Issues")

    response = auth_client.get(f"/api/volumes/{data['volume'].id}/issues")

    assert response.status_code == 404
    assert response.json() == {"detail": "Volume not found"}


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
