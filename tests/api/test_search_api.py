from app.api.search import _get_allowed_library_ids
from app.models.collection import Collection, CollectionItem
from app.models.comic import Volume
from app.models.credits import ComicCredit, Person
from app.models.pull_list import PullList, PullListItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series
from app.models.tags import Character, Location, Team
from app.models.user import User
from tests.factories import create_comic, create_library_with_root


def _seed_search_fixture(db, normal_user):
    visible_lib = create_library_with_root(db, "FindMe Visible Library", "/tmp/findme-visible")
    hidden_lib = create_library_with_root(db, "FindMe Hidden Library", "/tmp/findme-hidden")
    visible_root = visible_lib.active_root
    hidden_root = hidden_lib.active_root

    safe_series = Series(name="FindMe Safe Series", library_id=visible_lib.id)
    poisoned_series = Series(name="FindMe Banned Series", library_id=visible_lib.id)
    hidden_series = Series(name="FindMe Hidden Series", library_id=hidden_lib.id)
    db.add_all([safe_series, poisoned_series, hidden_series])
    db.flush()

    safe_vol = Volume(series_id=safe_series.id, volume_number=1)
    poisoned_vol = Volume(series_id=poisoned_series.id, volume_number=1)
    hidden_vol = Volume(series_id=hidden_series.id, volume_number=1)
    db.add_all([safe_vol, poisoned_vol, hidden_vol])
    db.flush()

    safe1 = create_comic(
        db, safe_vol, visible_root, "findme-safe-1.cbz",
        number="1",
        title="FindMe Safe #1",
        publisher="FindMe Publisher",
        format="mini-series",
        imprint="FindMe Imprint",
        age_rating="Teen",
        language_iso="en",
        filename="findme-safe-1.cbz",
    )
    safe2 = create_comic(
        db, safe_vol, visible_root, "findme-safe-2.cbz",
        number="2",
        title="FindMe Safe #2",
        publisher="FindMe Publisher",
        format="mini-series",
        imprint="FindMe Imprint",
        age_rating="Teen",
        language_iso="en",
        filename="findme-safe-2.cbz",
    )
    banned = create_comic(
        db, poisoned_vol, visible_root, "findme-banned.cbz",
        number="1",
        title="FindMe Banned",
        publisher="FindMe Banned Publisher",
        format="annual",
        imprint="FindMe Bad Imprint",
        age_rating="Mature 17+",
        language_iso="jp",
        filename="findme-banned.cbz",
    )
    hidden = create_comic(
        db, hidden_vol, hidden_root, "findme-hidden.cbz",
        number="1",
        title="FindMe Hidden",
        publisher="FindMe Hidden Publisher",
        format="one-shot",
        imprint="FindMe Hidden Imprint",
        age_rating="Teen",
        language_iso="fr",
        filename="findme-hidden.cbz",
    )

    safe_character = Character(name="FindMe Hero")
    banned_character = Character(name="FindMe Villain")
    safe_team = Team(name="FindMe Team")
    banned_team = Team(name="FindMe Evil Team")
    safe_location = Location(name="FindMe City")
    banned_location = Location(name="FindMe Forbidden City")
    db.add_all([
        safe_character,
        banned_character,
        safe_team,
        banned_team,
        safe_location,
        banned_location,
    ])
    db.flush()

    safe1.characters.append(safe_character)
    safe2.characters.append(safe_character)
    banned.characters.append(banned_character)

    safe1.teams.append(safe_team)
    safe2.teams.append(safe_team)
    banned.teams.append(banned_team)

    safe1.locations.append(safe_location)
    safe2.locations.append(safe_location)
    banned.locations.append(banned_location)

    safe_writer = Person(name="FindMe Writer")
    banned_writer = Person(name="FindMe Banned Writer")
    db.add_all([safe_writer, banned_writer])
    db.flush()

    db.add_all([
        ComicCredit(comic_id=safe1.id, person_id=safe_writer.id, role="writer"),
        ComicCredit(comic_id=safe2.id, person_id=safe_writer.id, role="writer"),
        ComicCredit(comic_id=banned.id, person_id=banned_writer.id, role="writer"),
    ])

    safe_collection = Collection(name="FindMe Safe Collection", description="safe")
    banned_collection = Collection(name="FindMe Banned Collection", description="banned")
    hidden_collection = Collection(name="FindMe Hidden Collection", description="hidden")
    db.add_all([safe_collection, banned_collection, hidden_collection])
    db.flush()

    db.add_all([
        CollectionItem(collection_id=safe_collection.id, comic_id=safe1.id),
        CollectionItem(collection_id=banned_collection.id, comic_id=banned.id),
        CollectionItem(collection_id=hidden_collection.id, comic_id=hidden.id),
    ])

    safe_reading_list = ReadingList(name="FindMe Safe Reading List", description="safe")
    banned_reading_list = ReadingList(name="FindMe Banned Reading List", description="banned")
    hidden_reading_list = ReadingList(name="FindMe Hidden Reading List", description="hidden")
    db.add_all([safe_reading_list, banned_reading_list, hidden_reading_list])
    db.flush()

    db.add_all([
        ReadingListItem(reading_list_id=safe_reading_list.id, comic_id=safe1.id, position=1),
        ReadingListItem(reading_list_id=banned_reading_list.id, comic_id=banned.id, position=1),
        ReadingListItem(reading_list_id=hidden_reading_list.id, comic_id=hidden.id, position=1),
    ])

    other_user = User(
        username="findme-other-user",
        email="findme-other@example.com",
        hashed_password="x",
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()

    safe_pull = PullList(user_id=normal_user.id, name="FindMe Safe Pull", description="safe")
    banned_pull = PullList(user_id=normal_user.id, name="FindMe Banned Pull", description="banned")
    other_pull = PullList(user_id=other_user.id, name="FindMe Other Pull", description="other")
    db.add_all([safe_pull, banned_pull, other_pull])
    db.flush()

    db.add_all([
        PullListItem(pull_list_id=safe_pull.id, comic_id=safe1.id, sort_order=1),
        PullListItem(pull_list_id=banned_pull.id, comic_id=banned.id, sort_order=1),
        PullListItem(pull_list_id=other_pull.id, comic_id=safe1.id, sort_order=1),
    ])

    normal_user.accessible_libraries.append(visible_lib)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False

    db.commit()

    return {
        "visible_lib": visible_lib,
        "safe_series": safe_series,
        "poisoned_series": poisoned_series,
        "hidden_series": hidden_series,
    }


def test_get_allowed_library_ids_helper(db, normal_user, admin_user):
    lib = create_library_with_root(db, "search-helper-lib", "/tmp/search-helper")

    normal_user.accessible_libraries.append(lib)
    db.commit()

    assert _get_allowed_library_ids(admin_user) is None
    assert _get_allowed_library_ids(normal_user) == [lib.id]


def test_search_suggestions_respects_rls_age_and_field_routes(auth_client, db, normal_user):
    _seed_search_fixture(db, normal_user)

    def suggest(field, value):
        response = auth_client.get(f"/api/search/suggestions?field={field}&query={value}")
        assert response.status_code == 200
        return response.json()

    series = suggest("series", "FindMe")
    assert "FindMe Safe Series" in series
    assert "FindMe Banned Series" not in series
    assert "FindMe Hidden Series" not in series

    assert suggest("library", "FindMe") == ["FindMe Visible Library"]
    assert suggest("publisher", "Publisher") == ["FindMe Publisher"]
    assert suggest("character", "FindMe") == ["FindMe Hero"]
    assert suggest("team", "FindMe") == ["FindMe Team"]
    assert suggest("writer", "FindMe") == ["FindMe Writer"]
    assert suggest("collection", "FindMe") == ["FindMe Safe Collection"]
    assert suggest("location", "FindMe") == ["FindMe City"]
    assert suggest("format", "mini") == ["mini-series"]
    assert suggest("imprint", "FindMe") == ["FindMe Imprint"]
    assert suggest("age_rating", "Teen") == ["Teen"]
    assert suggest("language", "en") == ["en"]
    assert suggest("reading_list", "FindMe") == ["FindMe Safe Reading List"]
    assert suggest("pull_list", "FindMe") == ["FindMe Safe Pull"]
    assert suggest("not-a-field", "FindMe") == []


def test_search_suggestions_validation(auth_client):
    response = auth_client.get("/api/search/suggestions?field=series&query=")
    assert response.status_code == 422


def test_quick_search_segments_and_scoping(auth_client, db, normal_user):
    _seed_search_fixture(db, normal_user)

    response = auth_client.get("/api/search/quick?q=FindMe")

    assert response.status_code == 200
    payload = response.json()

    assert [row["name"] for row in payload["series"]] == ["FindMe Safe Series"]
    assert [row["name"] for row in payload["collections"]] == ["FindMe Safe Collection"]
    assert [row["name"] for row in payload["reading_lists"]] == ["FindMe Safe Reading List"]

    assert [row["name"] for row in payload["people"]] == ["FindMe Writer"]
    assert [row["name"] for row in payload["characters"]] == ["FindMe Hero"]
    assert [row["name"] for row in payload["teams"]] == ["FindMe Team"]
    assert [row["name"] for row in payload["locations"]] == ["FindMe City"]
    assert [row["name"] for row in payload["pull_lists"]] == ["FindMe Safe Pull"]


def test_search_suggestions_and_quick_respect_library_metadata_toggles(auth_client, db, normal_user):
    data = _seed_search_fixture(db, normal_user)
    data["visible_lib"].parse_collections = False
    data["visible_lib"].parse_reading_lists = False
    db.commit()

    collection_suggest = auth_client.get("/api/search/suggestions?field=collection&query=FindMe")
    reading_list_suggest = auth_client.get("/api/search/suggestions?field=reading_list&query=FindMe")
    quick = auth_client.get("/api/search/quick?q=FindMe")

    assert collection_suggest.status_code == 200
    assert reading_list_suggest.status_code == 200
    assert quick.status_code == 200
    assert collection_suggest.json() == []
    assert reading_list_suggest.json() == []
    assert quick.json()["collections"] == []
    assert quick.json()["reading_lists"] == []


def test_quick_search_validation_for_short_query(auth_client):
    response = auth_client.get("/api/search/quick?q=f")
    assert response.status_code == 422


def test_quick_search_superuser_sees_all_series(admin_client, db, normal_user):
    _seed_search_fixture(db, normal_user)

    response = admin_client.get("/api/search/quick?q=FindMe")

    assert response.status_code == 200
    names = {row["name"] for row in response.json()["series"]}
    assert "FindMe Safe Series" in names
    assert "FindMe Banned Series" in names
    assert "FindMe Hidden Series" in names
