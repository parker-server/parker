from sqlalchemy import event

from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.models.tags import Character, Team
from tests.factories import create_library_with_root


def _comic(
    volume,
    *,
    number: str,
    title: str,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    story_arc: str | None = None,
    series_group: str | None = None,
    age_rating: str | None = None,
) -> Comic:
    slug = title.lower().replace(" ", "-").replace("#", "")
    root = volume.series.library.active_root
    return Comic(
        volume=volume,
        number=number,
        title=title,
        year=year,
        month=month,
        day=day,
        story_arc=story_arc,
        series_group=series_group,
        age_rating=age_rating,
        filename=f"{slug}.cbz",
        library_root_id=root.id,
        relative_path=f"{slug}.cbz",
        page_count=22,
    )


def _seed_timeline_fixture(db, normal_user):
    library = create_library_with_root(db, "Timeline Library", "/tmp/timeline-library")
    hidden_library = create_library_with_root(db, "Hidden Timeline Library", "/tmp/hidden-timeline-library")

    action = Series(name="Action Timeline", library=library)
    adventures = Series(name="Adventures Timeline", library=library)
    hidden_series = Series(name="Hidden Timeline", library=hidden_library)
    db.add_all([action, adventures, hidden_series])
    db.flush()

    action_volume = Volume(series=action, volume_number=1)
    adventures_volume = Volume(series=adventures, volume_number=1)
    hidden_volume = Volume(series=hidden_series, volume_number=1)
    db.add_all([action_volume, adventures_volume, hidden_volume])
    db.flush()

    issue_one = _comic(
        action_volume,
        number="1",
        title="Timeline Dawn",
        year=1938,
        month=6,
        day=1,
        series_group="Golden Age",
        age_rating="Teen",
    )
    issue_two = _comic(
        adventures_volume,
        number="436",
        title="Timeline Millennium",
        year=1988,
        month=1,
        story_arc="Millennium",
        age_rating="Teen",
    )
    issue_three = _comic(
        action_volume,
        number="999",
        title="Timeline Undated",
        age_rating="Teen",
    )
    hidden_issue = _comic(
        hidden_volume,
        number="1",
        title="Timeline Hidden",
        year=1999,
        age_rating="Teen",
    )
    db.add_all([issue_one, issue_two, issue_three, hidden_issue])
    db.flush()

    hero = Character(name="Timeline Hero")
    team = Team(name="Timeline Team")
    db.add_all([hero, team])
    db.flush()

    for comic in [issue_one, issue_two, issue_three, hidden_issue]:
        comic.characters.append(hero)
    issue_two.teams.append(team)

    event = ReadingList(name="Timeline Event", description="ordered event")
    collection = Collection(name="Timeline Collection", description="group")
    db.add_all([event, collection])
    db.flush()

    db.add_all(
        [
            ReadingListItem(reading_list_id=event.id, comic_id=issue_two.id, position=12),
            CollectionItem(collection_id=collection.id, comic_id=issue_one.id),
        ]
    )

    normal_user.accessible_libraries.append(library)
    db.commit()

    return {
        "library": library,
        "hero": hero,
        "team": team,
        "issue_one": issue_one,
        "issue_two": issue_two,
        "issue_three": issue_three,
        "hidden_issue": hidden_issue,
    }


def test_character_timeline_orders_issues_and_adds_metadata_context(auth_client, db, normal_user):
    data = _seed_timeline_fixture(db, normal_user)

    response = auth_client.get("/api/timelines?type=character&name=Timeline%20Hero")

    assert response.status_code == 200
    payload = response.json()

    assert payload["subject"] == {"type": "character", "name": "Timeline Hero"}
    assert payload["summary"]["total_issues"] == 3
    assert payload["summary"]["dated_issues"] == 2
    assert payload["summary"]["undated_issues"] == 1
    assert payload["summary"]["series_count"] == 2
    assert payload["summary"]["story_arc_count"] == 1
    assert payload["summary"]["reading_list_count"] == 1
    assert payload["summary"]["collection_count"] == 1
    assert payload["summary"]["start_year"] == 1938
    assert payload["summary"]["end_year"] == 1988

    assert [group["year"] for group in payload["years"]] == [1938, 1988]
    assert payload["years"][0]["entries"][0]["id"] == data["issue_one"].id
    assert "series_group" not in payload["years"][0]["entries"][0]
    assert payload["years"][0]["entries"][0]["collections"][0]["name"] == "Timeline Collection"
    assert payload["years"][1]["entries"][0]["id"] == data["issue_two"].id
    assert payload["years"][1]["entries"][0]["story_arc"] == "Millennium"
    assert payload["years"][1]["entries"][0]["reading_lists"][0]["name"] == "Timeline Event"
    assert payload["years"][1]["entries"][0]["reading_lists"][0]["position"] == 12
    assert payload["undated_entries"][0]["id"] == data["issue_three"].id

    assert payload["milestones"]["first_issue"]["id"] == data["issue_one"].id
    assert payload["milestones"]["latest_issue"]["id"] == data["issue_two"].id
    assert payload["milestones"]["first_story_arcs"][0]["name"] == "Millennium"
    assert payload["milestones"]["first_reading_lists"][0]["name"] == "Timeline Event"
    assert payload["milestones"]["first_reading_lists"][0]["id"]
    assert payload["milestones"]["first_collections"][0]["name"] == "Timeline Collection"
    assert payload["milestones"]["first_collections"][0]["id"]


def test_team_timeline_supports_team_subjects(auth_client, db, normal_user):
    data = _seed_timeline_fixture(db, normal_user)

    response = auth_client.get("/api/timelines?type=team&name=Timeline%20Team")

    assert response.status_code == 200
    payload = response.json()

    assert payload["subject"] == {"type": "team", "name": "Timeline Team"}
    assert payload["summary"]["total_issues"] == 1
    assert payload["years"][0]["entries"][0]["id"] == data["issue_two"].id


def test_timeline_suggestions_are_scoped_to_visible_character_and_team_tags(auth_client, db, normal_user):
    _seed_timeline_fixture(db, normal_user)

    response = auth_client.get("/api/timelines/suggestions?q=Timeline")

    assert response.status_code == 200
    payload = response.json()

    assert {"type": "character", "name": "Timeline Hero"} in payload
    assert {"type": "team", "name": "Timeline Team"} in payload


def test_timeline_respects_library_access_and_age_restrictions(auth_client, db, normal_user):
    data = _seed_timeline_fixture(db, normal_user)

    restricted_series = Series(name="Restricted Timeline", library=data["library"])
    restricted_volume = Volume(series=restricted_series, volume_number=1)
    restricted_issue = _comic(
        restricted_volume,
        number="1",
        title="Timeline Restricted",
        year=2000,
        age_rating="Mature 17+",
    )
    restricted_issue.characters.append(data["hero"])
    db.add_all([restricted_series, restricted_volume, restricted_issue])

    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/timelines?type=character&name=Timeline%20Hero")

    assert response.status_code == 200
    payload = response.json()
    ids = {
        entry["id"]
        for group in payload["years"]
        for entry in group["entries"]
    }
    ids.update(entry["id"] for entry in payload["undated_entries"])

    assert data["hidden_issue"].id not in ids
    assert restricted_issue.id not in ids


def test_timeline_rejects_non_mvp_subject_types(auth_client, db, normal_user):
    _seed_timeline_fixture(db, normal_user)

    response = auth_client.get("/api/timelines?type=location&name=Timeline%20City")

    assert response.status_code == 422


def _timeline_lookup_plan(client, db, url: str, link_table: str) -> str:
    """EXPLAIN QUERY PLAN for the query that loads a timeline's comics."""
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    statement, parameters = next(
        (statement, parameters)
        for statement, parameters in captured
        if f"IN (SELECT {link_table}.comic_id" in statement
    )
    rows = db.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).fetchall()
    return " | ".join(row[-1] for row in rows)


def test_timeline_finds_subject_comics_through_the_subject_index(auth_client, db, normal_user):
    # Checking every comic for the subject's name took up to 0.15s on a 73k-issue library.
    _seed_timeline_fixture(db, normal_user)

    character_plan = _timeline_lookup_plan(
        auth_client, db, "/api/timelines?type=character&name=Timeline%20Hero", "comic_characters"
    )
    team_plan = _timeline_lookup_plan(auth_client, db, "/api/timelines?type=team&name=Timeline%20Team", "comic_teams")

    assert "idx_comic_characters_character_comic (character_id=?)" in character_plan
    assert "idx_comic_teams_team_comic (team_id=?)" in team_plan
