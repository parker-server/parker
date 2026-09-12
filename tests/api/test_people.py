from app.models.comic import Volume
from app.models.credits import ComicCredit, Person
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root


def _seed_person_fixture(db, normal_user):
    visible_library = create_library_with_root(db, "Person Visible Library", "/tmp/person-visible")
    hidden_library = create_library_with_root(db, "Person Hidden Library", "/tmp/person-hidden")
    visible_root = visible_library.active_root
    hidden_root = hidden_library.active_root

    writer_series = Series(name="Person Writer Run", library=visible_library)
    art_series = Series(name="Person Art Run", library=visible_library)
    banned_series = Series(name="Person Banned Run", library=visible_library)
    hidden_series = Series(name="Person Hidden Run", library=hidden_library)
    db.add_all([writer_series, art_series, banned_series, hidden_series])
    db.flush()

    writer_volume = Volume(series=writer_series, volume_number=1)
    art_volume = Volume(series=art_series, volume_number=1)
    banned_volume = Volume(series=banned_series, volume_number=1)
    hidden_volume = Volume(series=hidden_series, volume_number=1)
    db.add_all([writer_volume, art_volume, banned_volume, hidden_volume])
    db.flush()

    writer_one = create_comic(
        db,
        writer_volume,
        visible_root,
        "person-writer-1.cbz",
        number="1",
        title="Writer One",
        year=1980,
        publisher="Visible Publisher",
        age_rating="Teen",
        filename="person-writer-1.cbz",
    )
    writer_two = create_comic(
        db,
        writer_volume,
        visible_root,
        "person-writer-2.cbz",
        number="2",
        title="Writer Two",
        year=1981,
        publisher="Visible Publisher",
        age_rating="Teen",
        filename="person-writer-2.cbz",
    )
    penciller_one = create_comic(
        db,
        art_volume,
        visible_root,
        "person-art-1.cbz",
        number="1",
        title="Art One",
        year=1985,
        publisher="Art Publisher",
        age_rating="Teen",
        filename="person-art-1.cbz",
    )
    banned = create_comic(
        db,
        banned_volume,
        visible_root,
        "person-banned.cbz",
        number="1",
        title="Banned",
        year=1990,
        publisher="Banned Publisher",
        age_rating="Mature 17+",
        filename="person-banned.cbz",
    )
    hidden = create_comic(
        db,
        hidden_volume,
        hidden_root,
        "person-hidden.cbz",
        number="1",
        title="Hidden",
        year=1995,
        publisher="Hidden Publisher",
        age_rating="Teen",
        filename="person-hidden.cbz",
    )

    person = Person(name="Person Page Person")
    hidden_only_person = Person(name="Hidden Only Person")
    db.add_all([person, hidden_only_person])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=writer_one.id, person_id=person.id, role="writer"),
        ComicCredit(comic_id=writer_one.id, person_id=person.id, role="inker"),
        ComicCredit(comic_id=writer_two.id, person_id=person.id, role="writer"),
        ComicCredit(comic_id=penciller_one.id, person_id=person.id, role="penciller"),
        ComicCredit(comic_id=banned.id, person_id=person.id, role="writer"),
        ComicCredit(comic_id=hidden.id, person_id=person.id, role="writer"),
        ComicCredit(comic_id=hidden.id, person_id=hidden_only_person.id, role="writer"),
    ])

    normal_user.accessible_libraries.append(visible_library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    return {
        "person": person,
        "hidden_only_person": hidden_only_person,
    }


def test_person_detail_groups_visible_credits_by_role(auth_client, db, normal_user):
    data = _seed_person_fixture(db, normal_user)

    response = auth_client.get(f"/api/people/{data['person'].id}")

    assert response.status_code == 200
    payload = response.json()

    assert payload["name"] == "Person Page Person"
    assert payload["total_issues"] == 3
    assert payload["total_series"] == 2
    assert payload["start_year"] == 1980
    assert payload["end_year"] == 1985
    assert {row["name"] for row in payload["top_publishers"]} == {"Visible Publisher", "Art Publisher"}

    roles = {row["role"]: row for row in payload["roles"]}
    assert list(roles) == ["writer", "penciller", "inker"]
    assert roles["writer"]["issue_count"] == 2
    assert roles["writer"]["series_count"] == 1
    assert roles["writer"]["series"][0]["name"] == "Person Writer Run"
    assert roles["writer"]["series"][0]["thumbnail_path"].startswith("/api/comics/")
    assert roles["penciller"]["issue_count"] == 1
    assert roles["penciller"]["series"][0]["name"] == "Person Art Run"
    assert roles["inker"]["issue_count"] == 1


def test_person_detail_hides_people_with_no_visible_credits(auth_client, db, normal_user):
    data = _seed_person_fixture(db, normal_user)

    response = auth_client.get(f"/api/people/{data['hidden_only_person'].id}")

    assert response.status_code == 404


def test_person_detail_superuser_can_view_all_credits(admin_client, db, normal_user):
    data = _seed_person_fixture(db, normal_user)

    response = admin_client.get(f"/api/people/{data['person'].id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_issues"] == 5
    assert payload["total_series"] == 4
    assert payload["end_year"] == 1995
    assert "Banned Publisher" in {row["name"] for row in payload["top_publishers"]}
    assert "Hidden Publisher" in {row["name"] for row in payload["top_publishers"]}
