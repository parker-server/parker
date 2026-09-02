import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import literal_column

from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.interactions import UserComicRating
from app.models.series import Series
from app.models.user import User
from app.schemas.search import SearchFilter, SearchRequest
from app.services.search import SearchService
from tests.factories import create_comic, create_library_with_root


def _seed_search_graph(db):
    library = create_library_with_root(db, "search-lib", "/tmp/search-lib")
    root = library.active_root
    series = Series(name="Search Series", library=library)
    volume = Volume(series=series, volume_number=1)

    db.add_all([series, volume])
    db.flush()

    alpha = create_comic(
        db, volume, root, "alpha.cbz",
        number="1",
        title="Alpha Dawn",
        year=2020,
        publisher="Marvel",
        filename="alpha.cbz",
    )
    beta = create_comic(
        db, volume, root, "beta.cbz",
        number="2",
        title="Beta Night",
        year=2021,
        publisher="DC",
        filename="beta.cbz",
    )

    db.commit()

    for obj in (library, series, volume, alpha, beta):
        db.refresh(obj)

    return {
        "library": library,
        "series": series,
        "volume": volume,
        "alpha": alpha,
        "beta": beta,
    }


def _sql(expr):
    return str(expr.compile(compile_kwargs={"literal_binds": True}))


ADVANCED_SEARCH_TEXT_OPERATORS = [
    "equal",
    "not_equal",
    "contains",
    "does_not_contain",
    "must_contain",
    "is_empty",
    "is_not_empty",
]
ADVANCED_SEARCH_NUMERIC_OPERATORS = [
    "equal",
    "not_equal",
    "at_least",
    "at_most",
    "is_empty",
    "is_not_empty",
]
ADVANCED_SEARCH_NAME_OPERATORS = [
    "equal",
    "not_equal",
    "contains",
    "does_not_contain",
]
ADVANCED_SEARCH_FILTER_OPERATORS = [
    "equal",
    "not_equal",
    "contains",
    "does_not_contain",
    "is_empty",
    "is_not_empty",
]
ADVANCED_SEARCH_RELATIONSHIP_OPERATORS = [
    "equal",
    "not_equal",
    "contains",
    "does_not_contain",
    "must_contain",
    "is_empty",
    "is_not_empty",
]
ADVANCED_SEARCH_FIELD_OPERATORS = {
    "series": ADVANCED_SEARCH_NAME_OPERATORS,
    "title": ADVANCED_SEARCH_TEXT_OPERATORS,
    "publisher": ADVANCED_SEARCH_FILTER_OPERATORS,
    "year": ADVANCED_SEARCH_NUMERIC_OPERATORS,
    "format": ADVANCED_SEARCH_FILTER_OPERATORS,
    "imprint": ADVANCED_SEARCH_FILTER_OPERATORS,
    "summary": ADVANCED_SEARCH_TEXT_OPERATORS,
    "web": ADVANCED_SEARCH_TEXT_OPERATORS,
    "rating": ADVANCED_SEARCH_NUMERIC_OPERATORS,
    "parker_rating": ADVANCED_SEARCH_NUMERIC_OPERATORS,
    "age_rating": ADVANCED_SEARCH_FILTER_OPERATORS,
    "language": ADVANCED_SEARCH_FILTER_OPERATORS,
    "writer": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "penciller": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "inker": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "colorist": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "letterer": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "cover_artist": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "editor": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "character": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "team": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "location": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "genre": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "library": ADVANCED_SEARCH_NAME_OPERATORS,
    "collection": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "reading_list": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
    "pull_list": ADVANCED_SEARCH_RELATIONSHIP_OPERATORS,
}
ADVANCED_SEARCH_UI_FIELDS = list(ADVANCED_SEARCH_FIELD_OPERATORS)
TAG_INPUT_FIELDS = {
    "writer",
    "penciller",
    "inker",
    "colorist",
    "letterer",
    "cover_artist",
    "editor",
    "character",
    "team",
    "location",
    "genre",
    "library",
    "collection",
    "reading_list",
    "pull_list",
}


def _matrix_filter_value(field: str, operator: str):
    if operator in ["is_empty", "is_not_empty"]:
        return None
    if field in ["year", "rating", "parker_rating"]:
        return 4
    if field in TAG_INPUT_FIELDS or operator == "must_contain":
        return ["Alpha", "Beta"] if operator == "must_contain" else ["Alpha"]
    return "Alpha"


ADVANCED_SEARCH_UI_FIELD_OPERATOR_CASES = [
    (field, operator)
    for field, operators in ADVANCED_SEARCH_FIELD_OPERATORS.items()
    for operator in operators
]


def test_search_service_filters_by_context_and_title(db, normal_user):
    data = _seed_search_graph(db)
    service = SearchService(db, normal_user)

    request = SearchRequest(
        match="all",
        filters=[SearchFilter(field="title", operator="contains", value="Alpha")],
        context_library_id=data["library"].id,
        sort_by="year",
        sort_order="asc",
        limit=10,
        offset=0,
    )

    results = service.search(request)

    assert results["total"] == 1
    assert len(results["results"]) == 1
    assert results["results"][0]["id"] == data["alpha"].id
    assert results["results"][0]["series"] == data["series"].name


def test_search_service_filters_by_library_name_without_cross_join(db, normal_user):
    target_library = create_library_with_root(db, "Target Search Library", "/tmp/search-target-lib")
    other_library = create_library_with_root(db, "Other Search Library", "/tmp/search-other-lib")
    target_series = Series(name="Target Series", library=target_library)
    other_series = Series(name="Other Series", library=other_library)
    target_volume = Volume(series=target_series, volume_number=1)
    other_volume = Volume(series=other_series, volume_number=1)
    db.add_all([target_series, other_series, target_volume, other_volume])
    db.flush()

    target_comic = create_comic(
        db,
        target_volume,
        target_library.active_root,
        "target.cbz",
        number="1",
        title="Target Comic",
        filename="target.cbz",
    )
    create_comic(
        db,
        other_volume,
        other_library.active_root,
        "other.cbz",
        number="1",
        title="Other Comic",
        filename="other.cbz",
    )
    db.commit()

    service = SearchService(db, normal_user)
    request = SearchRequest(
        match="all",
        filters=[SearchFilter(field="library", operator="equal", value="Target Search Library")],
        sort_by="series",
        sort_order="asc",
        limit=10,
        offset=0,
    )

    results = service.search(request)

    assert results["total"] == 1
    assert [item["id"] for item in results["results"]] == [target_comic.id]


def test_search_service_match_any_combines_conditions_with_or(db, normal_user):
    _seed_search_graph(db)
    service = SearchService(db, normal_user)

    request = SearchRequest(
        match="any",
        filters=[
            SearchFilter(field="title", operator="contains", value="Alpha"),
            SearchFilter(field="publisher", operator="equal", value="DC"),
        ],
        sort_by="title",
        sort_order="asc",
        limit=10,
        offset=0,
    )

    results = service.search(request)

    assert results["total"] == 2
    assert {item["title"] for item in results["results"]} == {"Alpha Dawn", "Beta Night"}


def test_search_service_build_condition_routes_for_relationship_fields(db, normal_user):
    service = SearchService(db, normal_user)

    filters = [
        SearchFilter(field="writer", operator="contains", value="Morrison"),
        SearchFilter(field="character", operator="contains", value=["Batman"]),
        SearchFilter(field="collection", operator="contains", value="Favorites"),
        SearchFilter(field="reading_list", operator="contains", value="Roadmap"),
        SearchFilter(field="pull_list", operator="contains", value="Wednesday"),
    ]

    for filter_item in filters:
        expression = service._build_condition(filter_item)
        assert expression is not None


def test_search_service_fts_condition_handles_missing_table(db, normal_user):
    service = SearchService(db, normal_user)
    assert service._build_fts_condition("batman") is None


def test_search_service_fts_condition_handles_positive_and_negative_paths(normal_user):
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = [1, 2, 3]

    mocked_db = MagicMock()
    mocked_db.execute.return_value = scalar_result

    service = SearchService(mocked_db, normal_user)

    positive = service._build_fts_condition("batman", operator="contains")
    negative = service._build_fts_condition("batman", operator="does_not_contain")

    assert positive is not None
    assert negative is not None

    scalar_result.scalars.return_value.all.return_value = []
    all_valid = service._build_fts_condition("batman", operator="does_not_contain")

    assert all_valid is None


def test_search_service_applies_age_filter_and_skips_invalid_filters(db, normal_user):
    _seed_search_graph(db)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = True
    db.commit()
    db.refresh(normal_user)

    service = SearchService(db, normal_user)
    request = SearchRequest(
        match="all",
        filters=[SearchFilter(field="title", operator="contains", value=None)],
        sort_by="created",
        sort_order="desc",
        limit=10,
        offset=0,
    )

    results = service.search(request)
    assert results["total"] == 2
    assert len(results["results"]) == 2


def test_search_service_filters_and_sorts_by_parker_rating(db, normal_user):
    data = _seed_search_graph(db)
    other_alpha_user = User(
        username="alpha-rater",
        email="alpha-rater@example.com",
        hashed_password="x",
        is_superuser=False,
        is_active=True,
    )
    other_beta_user = User(
        username="beta-rater",
        email="beta-rater@example.com",
        hashed_password="x",
        is_superuser=False,
        is_active=True,
    )
    db.add_all([other_alpha_user, other_beta_user])
    db.flush()

    db.add_all([
        UserComicRating(user_id=normal_user.id, comic_id=data["alpha"].id, rating=5),
        UserComicRating(user_id=normal_user.id, comic_id=data["beta"].id, rating=3),
        UserComicRating(user_id=other_alpha_user.id, comic_id=data["alpha"].id, rating=4),
        UserComicRating(user_id=other_beta_user.id, comic_id=data["beta"].id, rating=2),
    ])
    db.commit()

    service = SearchService(db, normal_user)
    request = SearchRequest(
        match="all",
        filters=[SearchFilter(field="parker_rating", operator="at_least", value=4)],
        sort_by="parker_rating",
        sort_order="desc",
        limit=10,
        offset=0,
    )

    results = service.search(request)

    assert results["total"] == 1
    assert len(results["results"]) == 1
    assert results["results"][0]["id"] == data["alpha"].id
    assert results["results"][0]["rating_mode"] == "parker"
    assert results["results"][0]["rating_value"] == 4.5
    assert results["results"][0]["parker_rating_average"] == 4.5
    assert results["results"][0]["parker_rating_count"] == 2


def test_search_service_filters_missing_release_year(db, normal_user):
    library = create_library_with_root(db, "search-missing-year-lib", "/tmp/search-missing-year-lib")
    root = library.active_root
    series = Series(name="Missing Year Search Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    missing_year = create_comic(
        db, volume, root, "missing-year.cbz",
        number="1",
        title="Missing Year",
        year=None,
        filename="missing-year.cbz",
    )
    create_comic(
        db, volume, root, "with-year.cbz",
        number="2",
        title="With Year",
        year=2024,
        filename="with-year.cbz",
    )
    db.commit()

    service = SearchService(db, normal_user)
    request = SearchRequest(
        match="all",
        filters=[SearchFilter(field="year", operator="is_empty")],
        sort_by="series",
        sort_order="asc",
        limit=10,
        offset=0,
    )

    results = service.search(request)

    assert results["total"] == 1
    assert [item["id"] for item in results["results"]] == [missing_year.id]


def test_search_service_applies_creator_not_equal_with_other_filters(db, normal_user):
    library = create_library_with_root(db, "search-credit-not-equal-lib", "/tmp/search-credit-not-equal-lib")
    root = library.active_root
    series = Series(name="Uncle Sam", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    alex_ross = Person(name="Alex Ross")
    other_artist = Person(name="Other Artist")
    db.add_all([alex_ross, other_artist])
    db.flush()

    uncle_sam_one = create_comic(
        db,
        volume,
        root,
        "uncle-sam-1.cbz",
        number="1",
        title="Uncle Sam #1",
        language_iso="en",
        filename="uncle-sam-1.cbz",
    )
    uncle_sam_two = create_comic(
        db,
        volume,
        root,
        "uncle-sam-2.cbz",
        number="2",
        title="Uncle Sam #2",
        language_iso="en",
        filename="uncle-sam-2.cbz",
    )
    non_ross_comic = create_comic(
        db,
        volume,
        root,
        "non-ross.cbz",
        number="3",
        title="Not Ross",
        language_iso="en",
        filename="non-ross.cbz",
    )
    french_comic = create_comic(
        db,
        volume,
        root,
        "french.cbz",
        number="4",
        title="French Comic",
        language_iso="fr",
        filename="french.cbz",
    )
    db.add_all([
        ComicCredit(comic_id=uncle_sam_one.id, person_id=alex_ross.id, role="penciller"),
        ComicCredit(comic_id=uncle_sam_two.id, person_id=alex_ross.id, role="penciller"),
        ComicCredit(comic_id=non_ross_comic.id, person_id=other_artist.id, role="penciller"),
        ComicCredit(comic_id=french_comic.id, person_id=other_artist.id, role="penciller"),
    ])
    db.commit()

    service = SearchService(db, normal_user)
    request = SearchRequest(
        match="all",
        filters=[
            SearchFilter(field="language", operator="equal", value="en"),
            SearchFilter(field="penciller", operator="not_equal", value="Alex Ross"),
        ],
        sort_by="series",
        sort_order="asc",
        limit=10,
        offset=0,
    )

    results = service.search(request)

    assert results["total"] == 1
    assert [item["id"] for item in results["results"]] == [non_ross_comic.id]


def test_advanced_search_operator_matrix_covers_template_fields():
    template = Path("app/templates/search.html").read_text()
    field_select_start = template.index('<select x-model="rule.field"')
    field_select_end = template.index("</select>", field_select_start)
    field_select = template[field_select_start:field_select_end]
    template_fields = set(re.findall(r'<option value="([^"]+)">', field_select))

    assert template_fields == set(ADVANCED_SEARCH_UI_FIELDS)


@pytest.mark.parametrize("field,operator", ADVANCED_SEARCH_UI_FIELD_OPERATOR_CASES)
def test_advanced_search_ui_operator_matrix_builds_backend_condition(normal_user, field, operator):
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = [1]
    mocked_db = MagicMock()
    mocked_db.execute.return_value = scalar_result

    service = SearchService(mocked_db, normal_user)
    service._parker_rating_column = Comic.community_rating

    expression = service._build_condition(
        SearchFilter(
            field=field,
            operator=operator,
            value=_matrix_filter_value(field, operator),
        )
    )

    assert expression is not None


def test_build_condition_handles_empty_operators_and_fts_routing(normal_user):
    service = SearchService(MagicMock(), normal_user)
    service._build_empty_condition = MagicMock(return_value="empty-expr")
    service._build_fts_condition = MagicMock(return_value="fts-expr")

    empty_filter = SimpleNamespace(field="title", operator="is_empty", value=None)
    not_empty_filter = SimpleNamespace(field="title", operator="is_not_empty", value=None)
    any_filter = SimpleNamespace(field="any", operator="contains", value="batman")
    summary_filter = SimpleNamespace(field="summary", operator="does_not_contain", value="spoiler")

    assert service._build_condition(empty_filter) == "empty-expr"
    service._build_empty_condition.assert_called_with("title", True)

    assert service._build_condition(not_empty_filter) == "empty-expr"
    service._build_empty_condition.assert_called_with("title", False)

    assert service._build_condition(any_filter) == "fts-expr"
    service._build_fts_condition.assert_called_with("batman", "contains")

    assert service._build_condition(summary_filter) == "fts-expr"
    service._build_fts_condition.assert_called_with("spoiler", "does_not_contain")


@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("series", "contains", "Search"),
        ("library", "contains", "Library"),
        ("title", "contains", "Alpha"),
        ("writer", "contains", "Moore"),
        ("team", "contains", ["Justice League"]),
        ("location", "contains", ["Gotham"]),
        ("genre", "contains", ["Superhero"]),
        ("collection", "contains", "Favorites"),
        ("reading_list", "contains", "Roadmap"),
        ("pull_list", "contains", "Weekly"),
    ],
)
def test_build_condition_supported_field_routes(normal_user, field, operator, value):
    service = SearchService(MagicMock(), normal_user)
    expression = service._build_condition(SearchFilter(field=field, operator=operator, value=value))
    assert expression is not None


def test_build_condition_returns_none_for_unhandled_cases(normal_user):
    service = SearchService(MagicMock(), normal_user)

    assert service._build_condition(SearchFilter(field="story_arc", operator="contains", value="Zero Year")) is None
    assert service._build_condition(SimpleNamespace(field="series", operator="contains", value=None)) is None


def test_build_simple_field_condition_operators():
    equal_expr = SearchService._build_simple_field_condition(Comic.publisher, "equal", "Marvel")
    not_equal_expr = SearchService._build_simple_field_condition(Comic.publisher, "not_equal", "DC")
    contains_expr = SearchService._build_simple_field_condition(Comic.publisher, "contains", "vel")
    not_contains_expr = SearchService._build_simple_field_condition(Comic.publisher, "does_not_contain", "Image")
    must_expr = SearchService._build_simple_field_condition(Comic.publisher, "must_contain", ["Mar", "vel"])

    assert "comics.publisher = 'Marvel'" in _sql(equal_expr)
    assert "comics.publisher != 'DC'" in _sql(not_equal_expr)
    assert "lower(comics.publisher) LIKE lower('%vel%')" in _sql(contains_expr)
    not_contains_sql = _sql(not_contains_expr)
    assert "Image" in not_contains_sql and "NOT" in not_contains_sql
    assert _sql(must_expr).count("LIKE") == 2
    assert SearchService._build_simple_field_condition(Comic.publisher, "unknown", "Marvel") is None


def test_build_numeric_field_condition_operators():
    equal_expr = SearchService._build_numeric_field_condition(Comic.community_rating, "equal", 4.5)
    not_equal_expr = SearchService._build_numeric_field_condition(Comic.community_rating, "not_equal", 3)
    at_least_expr = SearchService._build_numeric_field_condition(Comic.community_rating, "at_least", "4")
    at_most_expr = SearchService._build_numeric_field_condition(Comic.community_rating, "at_most", 2.5)

    assert "comics.community_rating = 4.5" in _sql(equal_expr)
    assert "comics.community_rating != 3.0" in _sql(not_equal_expr)
    assert "comics.community_rating >= 4.0" in _sql(at_least_expr)
    assert "comics.community_rating <= 2.5" in _sql(at_most_expr)
    assert SearchService._build_numeric_field_condition(Comic.community_rating, "contains", 4) is None
    assert SearchService._build_numeric_field_condition(Comic.community_rating, "equal", "bad") is None


def test_build_credit_condition_operators():
    equal_expr = SearchService._build_credit_condition("writer", "equal", "Alan Moore")
    not_equal_expr = SearchService._build_credit_condition("writer", "not_equal", ["Alan Moore", "Grant Morrison"])
    contains_expr = SearchService._build_credit_condition("writer", "contains", ["Alan", "Grant"])
    not_contains_expr = SearchService._build_credit_condition("writer", "does_not_contain", ["Alan"])
    must_expr = SearchService._build_credit_condition("writer", "must_contain", ["Alan", "Dave"])

    equal_sql = _sql(equal_expr)
    not_equal_sql = _sql(not_equal_expr)
    contains_sql = _sql(contains_expr)
    not_contains_sql = _sql(not_contains_expr)
    must_sql = _sql(must_expr)

    assert "comic_credits.role = 'writer'" in equal_sql
    assert "people.name = 'Alan Moore'" in equal_sql
    assert "NOT (EXISTS" in not_equal_sql
    assert "people.name IN ('Alan Moore', 'Grant Morrison')" in not_equal_sql
    assert " OR " in contains_sql
    assert "NOT (EXISTS" in not_contains_sql
    assert must_sql.count("EXISTS") >= 2
    assert SearchService._build_credit_condition("writer", "unknown", "x") is None


def test_build_tag_condition_operators():
    equal_expr = SearchService._build_tag_condition(Comic.characters, Series.name, "equal", "Batman")
    contains_expr = SearchService._build_tag_condition(Comic.characters, Series.name, "contains", ["Batman", "Robin"])
    not_contains_expr = SearchService._build_tag_condition(Comic.characters, Series.name, "does_not_contain", ["Joker"])
    not_equal_expr = SearchService._build_tag_condition(Comic.characters, Series.name, "not_equal", ["Joker"])
    must_expr = SearchService._build_tag_condition(Comic.characters, Series.name, "must_contain", ["Batman", "Robin"])

    assert "series.name = 'Batman'" in _sql(equal_expr)
    assert "IN ('Batman', 'Robin')" in _sql(contains_expr)
    assert "NOT (EXISTS" in _sql(not_contains_expr)
    assert "NOT (EXISTS" in _sql(not_equal_expr)
    assert _sql(must_expr).count("EXISTS") >= 2
    assert SearchService._build_tag_condition(Comic.characters, Series.name, "unknown", "x") is None


def test_collection_and_reading_list_condition_operators():
    collection_equal = SearchService._build_collection_condition("equal", ["Favorites"])
    collection_contains_list = SearchService._build_collection_condition("contains", ["A", "B"])
    collection_contains_value = SearchService._build_collection_condition("contains", "Fav")
    collection_not_equal = SearchService._build_collection_condition("not_equal", ["Favorites"])
    collection_not_contains = SearchService._build_collection_condition("does_not_contain", "Fav")
    collection_must = SearchService._build_collection_condition("must_contain", ["A", "B"])
    reading_equal = SearchService._build_reading_list_condition("equal", ["Roadmap"])
    reading_contains_list = SearchService._build_reading_list_condition("contains", ["A", "B"])
    reading_contains_value = SearchService._build_reading_list_condition("contains", "Road")
    reading_not_equal = SearchService._build_reading_list_condition("not_equal", ["Roadmap"])
    reading_not_contains = SearchService._build_reading_list_condition("does_not_contain", "Road")
    reading_must = SearchService._build_reading_list_condition("must_contain", ["A", "B"])

    assert "collections.name = 'Favorites'" in _sql(collection_equal)
    assert "collections.name IN ('A', 'B')" in _sql(collection_contains_list)
    assert "lower(collections.name) LIKE lower('%Fav%')" in _sql(collection_contains_value)
    assert "NOT (EXISTS" in _sql(collection_not_equal)
    assert "collections.name IN ('Favorites')" in _sql(collection_not_equal)
    assert "NOT (EXISTS" in _sql(collection_not_contains)
    assert _sql(collection_must).count("EXISTS") >= 2
    assert "reading_lists.name = 'Roadmap'" in _sql(reading_equal)
    assert "reading_lists.name IN ('A', 'B')" in _sql(reading_contains_list)
    assert "lower(reading_lists.name) LIKE lower('%Road%')" in _sql(reading_contains_value)
    assert "NOT (EXISTS" in _sql(reading_not_equal)
    assert "reading_lists.name IN ('Roadmap')" in _sql(reading_not_equal)
    assert "NOT (EXISTS" in _sql(reading_not_contains)
    assert _sql(reading_must).count("EXISTS") >= 2
    assert SearchService._build_collection_condition("unknown", "x") is None
    assert SearchService._build_reading_list_condition("unknown", "x") is None


def test_build_empty_condition_relationship_and_simple_paths(normal_user):
    service = SearchService(MagicMock(), normal_user)

    assert "NOT (EXISTS" in _sql(service._build_empty_condition("character", True))
    assert "EXISTS" in _sql(service._build_empty_condition("character", False))
    assert "NOT (EXISTS" in _sql(service._build_empty_condition("team", True))
    assert "EXISTS" in _sql(service._build_empty_condition("location", False))
    assert "NOT (EXISTS" in _sql(service._build_empty_condition("collection", True))
    assert "EXISTS" in _sql(service._build_empty_condition("reading_list", False))
    assert "comic_credits.role = 'writer'" in _sql(service._build_empty_condition("writer", True))
    assert "comic_credits.role = 'writer'" in _sql(service._build_empty_condition("writer", False))
    assert "NOT (EXISTS" in _sql(service._build_empty_condition("pull_list", True))

    empty_title = _sql(service._build_empty_condition("title", True))
    non_empty_title = _sql(service._build_empty_condition("title", False))
    assert "comics.title IS NULL" in empty_title and "comics.title = ''" in empty_title
    assert "comics.title IS NOT NULL" in non_empty_title and "comics.title != ''" in non_empty_title

    empty_year = _sql(service._build_empty_condition("year", True))
    non_empty_year = _sql(service._build_empty_condition("year", False))
    assert empty_year == "comics.year IS NULL"
    assert non_empty_year == "comics.year IS NOT NULL"

    service._parker_rating_column = Comic.community_rating
    assert "comics.community_rating IS NULL" in _sql(service._build_empty_condition("parker_rating", True))
    assert "comics.community_rating IS NOT NULL" in _sql(service._build_empty_condition("parker_rating", False))

    assert service._build_empty_condition("story_arc", True) is None


def test_build_pull_list_condition_scopes_to_current_user(normal_user):
    service = SearchService(MagicMock(), normal_user)

    equal_expr = service._build_pull_list_condition("equal", ["Weekly"])
    contains_list_expr = service._build_pull_list_condition("contains", ["A", "B"])
    contains_value_expr = service._build_pull_list_condition("contains", "Week")
    not_equal_expr = service._build_pull_list_condition("not_equal", ["Weekly"])
    not_contains_expr = service._build_pull_list_condition("does_not_contain", "Week")
    must_expr = service._build_pull_list_condition("must_contain", ["A", "B"])

    assert f"pull_lists.user_id = {normal_user.id}" in _sql(equal_expr)
    assert "pull_lists.name = 'Weekly'" in _sql(equal_expr)
    assert "pull_lists.name IN ('A', 'B')" in _sql(contains_list_expr)
    assert "lower(pull_lists.name) LIKE lower('%Week%')" in _sql(contains_value_expr)
    assert "NOT (EXISTS" in _sql(not_equal_expr)
    assert "pull_lists.name IN ('Weekly')" in _sql(not_equal_expr)
    assert "NOT (EXISTS" in _sql(not_contains_expr)
    assert _sql(must_expr).count(f"pull_lists.user_id = {normal_user.id}") == 2
    assert service._build_pull_list_condition("unknown", "x") is None


def test_fts_condition_builds_and_or_and_no_match_paths(normal_user):
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.side_effect = [[1, 2], [3], []]

    mocked_db = MagicMock()
    mocked_db.execute.return_value = scalar_result
    service = SearchService(mocked_db, normal_user)

    assert service._build_fts_condition(None, operator="contains") is None

    and_expr = service._build_fts_condition(["alpha", "beta"], operator="must_contain")
    or_expr = service._build_fts_condition(["alpha", "beta"], operator="contains")
    empty_expr = service._build_fts_condition("nomatch", operator="contains")

    assert "comics.id IN (1, 2)" in _sql(and_expr)
    assert "comics.id IN (3)" in _sql(or_expr)
    assert "comics.id = -1" in _sql(empty_expr)

    first_term = mocked_db.execute.call_args_list[0].args[1]["term"]
    second_term = mocked_db.execute.call_args_list[1].args[1]["term"]
    third_term = mocked_db.execute.call_args_list[2].args[1]["term"]
    assert first_term == '"alpha" AND "beta"'
    assert second_term == '"alpha" OR "beta"'
    assert third_term == '"nomatch" *'


class _OrderRecorder:
    def __init__(self):
        self.calls = []

    def order_by(self, *args):
        self.calls.append(args)
        return self


ISSUE_SECONDARY_SORT = [
    "volumes.volume_number ASC",
    "CAST(comics.number AS FLOAT) ASC",
    "comics.number ASC",
    "comics.id ASC",
]

SERIES_SECONDARY_SORT = [
    "series.name ASC",
    *ISSUE_SECONDARY_SORT,
]


@pytest.mark.parametrize(
    "sort_by,sort_order,expected_column,expected_secondary",
    [
        ("series", "desc", "series.name", ISSUE_SECONDARY_SORT),
        ("year", "asc", "comics.year", SERIES_SECONDARY_SORT),
        ("title", "asc", "comics.title", SERIES_SECONDARY_SORT),
        ("page_count", "desc", "comics.page_count", SERIES_SECONDARY_SORT),
        ("rating", "desc", "comics.community_rating", SERIES_SECONDARY_SORT),
        ("parker_rating", "desc", "parker_average", ["parker_count DESC", *SERIES_SECONDARY_SORT]),
        ("updated", "asc", "comics.updated_at", SERIES_SECONDARY_SORT),
        ("created", "desc", "comics.created_at", SERIES_SECONDARY_SORT),
        ("unknown", "asc", "comics.created_at", SERIES_SECONDARY_SORT),
    ],
)
def test_apply_sorting_chooses_expected_columns(sort_by, sort_order, expected_column, expected_secondary):
    recorder = _OrderRecorder()
    result = SearchService._apply_sorting(
        recorder,
        sort_by,
        sort_order,
        parker_rating_column=literal_column("parker_average"),
        parker_rating_count_column=literal_column("parker_count"),
    )

    assert result is recorder
    assert len(recorder.calls) == (1 + (1 if expected_secondary else 0))

    primary_sql = str(recorder.calls[0][0])
    assert expected_column in primary_sql
    assert (" DESC" in primary_sql) == (sort_order == "desc")

    if expected_secondary:
        secondary = [str(expr) for expr in recorder.calls[1]]
        assert secondary == expected_secondary


def test_format_comic_uses_thumbnail_helper(monkeypatch, db):
    data = _seed_search_graph(db)
    comic = data["alpha"]
    monkeypatch.setattr("app.services.search.get_thumbnail_url", lambda comic_id, updated_at: f"/thumb/{comic_id}")

    payload = SearchService._format_comic(comic)
    assert payload["id"] == comic.id
    assert payload["series"] == data["series"].name
    assert payload["thumbnail_path"] == f"/thumb/{comic.id}"


def test_format_comic_prefers_parker_rating_context(monkeypatch, db):
    data = _seed_search_graph(db)
    comic = data["alpha"]
    comic.community_rating = 4.8
    monkeypatch.setattr("app.services.search.get_thumbnail_url", lambda comic_id, updated_at: f"/thumb/{comic_id}")

    parker_payload = SearchService._format_comic(
        comic,
        prefer_parker_rating=True,
        parker_rating_average=4.25,
        parker_rating_count=3,
    )
    assert parker_payload["rating_mode"] == "parker"
    assert parker_payload["rating_value"] == 4.25
    assert parker_payload["parker_rating_count"] == 3

    unrated_payload = SearchService._format_comic(
        comic,
        prefer_parker_rating=True,
        parker_rating_average=None,
        parker_rating_count=0,
    )
    assert unrated_payload["rating_mode"] == "none"
    assert unrated_payload["rating_value"] is None
