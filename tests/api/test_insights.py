from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.library import Library
from app.models.series import Series


def _seed_creator_collab_fixture(db, normal_user):
    visible_lib = Library(name="Insights Visible", path="/tmp/insights-visible")
    hidden_lib = Library(name="Insights Hidden", path="/tmp/insights-hidden")
    db.add_all([visible_lib, hidden_lib])
    db.flush()

    safe_series = Series(name="Insights Safe Series", library_id=visible_lib.id)
    banned_series = Series(name="Insights Banned Series", library_id=visible_lib.id)
    hidden_series = Series(name="Insights Hidden Series", library_id=hidden_lib.id)
    db.add_all([safe_series, banned_series, hidden_series])
    db.flush()

    safe_volume = Volume(series_id=safe_series.id, volume_number=1)
    banned_volume = Volume(series_id=banned_series.id, volume_number=1)
    hidden_volume = Volume(series_id=hidden_series.id, volume_number=1)
    db.add_all([safe_volume, banned_volume, hidden_volume])
    db.flush()

    safe_comics = [
        Comic(
            volume_id=safe_volume.id,
            number="1",
            title="Safe #1",
            age_rating="Teen",
            filename="safe-1.cbz",
            file_path="/tmp/safe-1.cbz",
        ),
        Comic(
            volume_id=safe_volume.id,
            number="2",
            title="Safe #2",
            age_rating="Teen",
            filename="safe-2.cbz",
            file_path="/tmp/safe-2.cbz",
        ),
        Comic(
            volume_id=safe_volume.id,
            number="3",
            title="Safe #3",
            age_rating="Teen",
            filename="safe-3.cbz",
            file_path="/tmp/safe-3.cbz",
        ),
    ]
    banned_comics = [
        Comic(
            volume_id=banned_volume.id,
            number="1",
            title="Banned #1",
            age_rating="Mature 17+",
            filename="banned-1.cbz",
            file_path="/tmp/banned-1.cbz",
        ),
        Comic(
            volume_id=banned_volume.id,
            number="2",
            title="Banned #2",
            age_rating="Mature 17+",
            filename="banned-2.cbz",
            file_path="/tmp/banned-2.cbz",
        ),
    ]
    hidden_comics = [
        Comic(
            volume_id=hidden_volume.id,
            number="1",
            title="Hidden #1",
            age_rating="Teen",
            filename="hidden-1.cbz",
            file_path="/tmp/hidden-1.cbz",
        ),
        Comic(
            volume_id=hidden_volume.id,
            number="2",
            title="Hidden #2",
            age_rating="Teen",
            filename="hidden-2.cbz",
            file_path="/tmp/hidden-2.cbz",
        ),
    ]
    db.add_all(safe_comics + banned_comics + hidden_comics)
    db.flush()

    writer_a = Person(name="Insight Writer A")
    writer_b = Person(name="Insight Writer B")
    artist_x = Person(name="Insight Artist X")
    artist_y = Person(name="Insight Artist Y")
    artist_z = Person(name="Insight Artist Z")
    artist_hidden = Person(name="Insight Hidden Artist")
    db.add_all([writer_a, writer_b, artist_x, artist_y, artist_z, artist_hidden])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=safe_comics[0].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=safe_comics[1].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=safe_comics[2].id, person_id=writer_b.id, role="writer"),
        ComicCredit(comic_id=safe_comics[0].id, person_id=artist_x.id, role="penciller"),
        ComicCredit(comic_id=safe_comics[1].id, person_id=artist_x.id, role="penciller"),
        ComicCredit(comic_id=safe_comics[2].id, person_id=artist_y.id, role="penciller"),

        ComicCredit(comic_id=banned_comics[0].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=banned_comics[1].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=banned_comics[0].id, person_id=artist_z.id, role="penciller"),
        ComicCredit(comic_id=banned_comics[1].id, person_id=artist_z.id, role="penciller"),

        ComicCredit(comic_id=hidden_comics[0].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=hidden_comics[1].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=hidden_comics[0].id, person_id=artist_hidden.id, role="penciller"),
        ComicCredit(comic_id=hidden_comics[1].id, person_id=artist_hidden.id, role="penciller"),
    ])

    normal_user.accessible_libraries.append(visible_lib)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    return {
        "visible_lib": visible_lib,
        "hidden_lib": hidden_lib,
    }


def test_creator_collaborations_respect_rls_and_age_filters(auth_client, db, normal_user):
    _seed_creator_collab_fixture(db, normal_user)

    response = auth_client.get("/api/insights/creator-collaborations")

    assert response.status_code == 200
    payload = response.json()

    assert payload["pair_count"] == 1
    assert payload["max_shared_issues"] == 2
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["name"] == "Insight Writer A"
    assert payload["rows"][0]["total_shared"] == 2
    assert payload["rows"][0]["id"] > 0
    assert len(payload["columns"]) == 1
    assert payload["columns"][0]["name"] == "Insight Artist X"
    assert payload["columns"][0]["total_shared"] == 2
    assert payload["columns"][0]["id"] > 0
    assert payload["top_collaborations"][0]["person_a"] == "Insight Writer A"
    assert payload["top_collaborations"][0]["person_b"] == "Insight Artist X"
    assert payload["top_collaborations"][0]["shared_issues"] == 2


def test_creator_collaborations_library_filter_blocks_unauthorized_library(auth_client, db, normal_user):
    data = _seed_creator_collab_fixture(db, normal_user)

    response = auth_client.get(f"/api/insights/creator-collaborations?library_id={data['hidden_lib'].id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Library not found"


def test_creator_collaborations_superuser_sees_all_matching_pairs(admin_client, db, normal_user):
    data = _seed_creator_collab_fixture(db, normal_user)

    response = admin_client.get(
        f"/api/insights/creator-collaborations?library_id={data['hidden_lib'].id}&min_shared=2"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["pair_count"] == 1
    assert payload["pairs"][0]["person_a"] == "Insight Writer A"
    assert payload["pairs"][0]["person_b"] == "Insight Hidden Artist"
    assert payload["pairs"][0]["shared_issues"] == 2


def test_creator_collaborations_include_lower_threshold_pairs(auth_client, db, normal_user):
    _seed_creator_collab_fixture(db, normal_user)

    response = auth_client.get("/api/insights/creator-collaborations?min_shared=1")

    assert response.status_code == 200
    payload = response.json()

    assert payload["pair_count"] == 2
    names = {(pair["person_a"], pair["person_b"], pair["shared_issues"]) for pair in payload["pairs"]}
    assert ("Insight Writer A", "Insight Artist X", 2) in names
    assert ("Insight Writer B", "Insight Artist Y", 1) in names


def test_creator_collaborations_reject_limit_over_guardrail(auth_client, db, normal_user):
    _seed_creator_collab_fixture(db, normal_user)

    response = auth_client.get("/api/insights/creator-collaborations?limit=16")

    assert response.status_code == 422


def test_creator_collaborations_include_identity_fields_for_matrix_alignment(auth_client, db, normal_user):
    _seed_creator_collab_fixture(db, normal_user)

    response = auth_client.get("/api/insights/creator-collaborations")

    assert response.status_code == 200
    payload = response.json()

    row = payload["rows"][0]
    column = payload["columns"][0]
    pair = payload["pairs"][0]

    assert row["id"] > 0
    assert column["id"] > 0
    assert pair["person_a_id"] == row["id"]
    assert pair["person_b_id"] == column["id"]


def test_creator_collaboration_header_totals_match_visible_matrix(auth_client, db, normal_user):
    visible_lib = Library(name="Visible Matrix Totals", path="/tmp/visible-matrix-totals")
    db.add(visible_lib)
    db.flush()

    series = Series(name="Visible Matrix Totals Series", library_id=visible_lib.id)
    db.add(series)
    db.flush()

    volume = Volume(series_id=series.id, volume_number=1)
    db.add(volume)
    db.flush()

    comics = [
        Comic(
            volume_id=volume.id,
            number="1",
            title="Visible #1",
            age_rating="Teen",
            filename="visible-1.cbz",
            file_path="/tmp/visible-1.cbz",
        ),
        Comic(
            volume_id=volume.id,
            number="2",
            title="Visible #2",
            age_rating="Teen",
            filename="visible-2.cbz",
            file_path="/tmp/visible-2.cbz",
        ),
        Comic(
            volume_id=volume.id,
            number="3",
            title="Visible #3",
            age_rating="Teen",
            filename="visible-3.cbz",
            file_path="/tmp/visible-3.cbz",
        ),
        Comic(
            volume_id=volume.id,
            number="4",
            title="Visible #4",
            age_rating="Teen",
            filename="visible-4.cbz",
            file_path="/tmp/visible-4.cbz",
        ),
        Comic(
            volume_id=volume.id,
            number="5",
            title="Visible #5",
            age_rating="Teen",
            filename="visible-5.cbz",
            file_path="/tmp/visible-5.cbz",
        ),
    ]
    db.add_all(comics)
    db.flush()

    writer_a = Person(name="Visible Writer A")
    writer_b = Person(name="Visible Writer B")
    writer_c = Person(name="Visible Writer C")
    artist_x = Person(name="Visible Artist X")
    artist_y = Person(name="Visible Artist Y")
    db.add_all([writer_a, writer_b, writer_c, artist_x, artist_y])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=comics[0].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=comics[1].id, person_id=writer_a.id, role="writer"),
        ComicCredit(comic_id=comics[2].id, person_id=writer_b.id, role="writer"),
        ComicCredit(comic_id=comics[3].id, person_id=writer_b.id, role="writer"),
        ComicCredit(comic_id=comics[4].id, person_id=writer_c.id, role="writer"),
        ComicCredit(comic_id=comics[0].id, person_id=artist_x.id, role="penciller"),
        ComicCredit(comic_id=comics[1].id, person_id=artist_x.id, role="penciller"),
        ComicCredit(comic_id=comics[2].id, person_id=artist_y.id, role="penciller"),
        ComicCredit(comic_id=comics[3].id, person_id=artist_y.id, role="penciller"),
        ComicCredit(comic_id=comics[4].id, person_id=artist_x.id, role="penciller"),
    ])

    normal_user.accessible_libraries.append(visible_lib)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/insights/creator-collaborations?min_shared=1&limit=2")

    assert response.status_code == 200
    payload = response.json()

    assert [row["name"] for row in payload["rows"]] == ["Visible Writer A", "Visible Writer B"]
    assert [column["name"] for column in payload["columns"]] == ["Visible Artist X", "Visible Artist Y"]
    assert {(pair["person_a"], pair["person_b"], pair["shared_issues"]) for pair in payload["pairs"]} == {
        ("Visible Writer A", "Visible Artist X", 2),
        ("Visible Writer B", "Visible Artist Y", 2),
    }
    assert payload["rows"][0]["total_shared"] == 2
    assert payload["rows"][1]["total_shared"] == 2
    assert payload["columns"][0]["total_shared"] == 2
    assert payload["columns"][1]["total_shared"] == 2
