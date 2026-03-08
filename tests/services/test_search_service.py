from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.schemas.search import SearchFilter, SearchRequest
from app.services.search import SearchService


def _seed_search_graph(db):
    library = Library(name="search-lib", path="/tmp/search-lib")
    series = Series(name="Search Series", library=library)
    volume = Volume(series=series, volume_number=1)

    db.add_all([library, series, volume])
    db.flush()

    alpha = Comic(
        volume_id=volume.id,
        number="1",
        title="Alpha Dawn",
        year=2020,
        publisher="Marvel",
        filename="alpha.cbz",
        file_path="/tmp/alpha.cbz",
    )
    beta = Comic(
        volume_id=volume.id,
        number="2",
        title="Beta Night",
        year=2021,
        publisher="DC",
        filename="beta.cbz",
        file_path="/tmp/beta.cbz",
    )

    db.add_all([alpha, beta])
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

    assert "comics.publisher = 'Marvel'" in _sql(equal_expr)
    assert "comics.publisher != 'DC'" in _sql(not_equal_expr)
    assert "lower(comics.publisher) LIKE lower('%vel%')" in _sql(contains_expr)
    not_contains_sql = _sql(not_contains_expr)
    assert "Image" in not_contains_sql and "NOT" in not_contains_sql
    assert SearchService._build_simple_field_condition(Comic.publisher, "must_contain", "Marvel") is None


def test_build_credit_condition_operators():
    equal_expr = SearchService._build_credit_condition("writer", "equal", "Alan Moore")
    contains_expr = SearchService._build_credit_condition("writer", "contains", ["Alan", "Grant"])
    not_contains_expr = SearchService._build_credit_condition("writer", "does_not_contain", ["Alan"])
    must_expr = SearchService._build_credit_condition("writer", "must_contain", ["Alan", "Dave"])

    equal_sql = _sql(equal_expr)
    contains_sql = _sql(contains_expr)
    not_contains_sql = _sql(not_contains_expr)
    must_sql = _sql(must_expr)

    assert "comic_credits.role = 'writer'" in equal_sql
    assert "people.name = 'Alan Moore'" in equal_sql
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
    collection_equal = SearchService._build_collection_condition("equal", "Favorites")
    collection_contains_list = SearchService._build_collection_condition("contains", ["A", "B"])
    collection_contains_value = SearchService._build_collection_condition("contains", "Fav")
    reading_equal = SearchService._build_reading_list_condition("equal", "Roadmap")
    reading_contains_list = SearchService._build_reading_list_condition("contains", ["A", "B"])
    reading_contains_value = SearchService._build_reading_list_condition("contains", "Road")

    assert "collections.name = 'Favorites'" in _sql(collection_equal)
    assert "collections.name IN ('A', 'B')" in _sql(collection_contains_list)
    assert "lower(collections.name) LIKE lower('%Fav%')" in _sql(collection_contains_value)
    assert "reading_lists.name = 'Roadmap'" in _sql(reading_equal)
    assert "reading_lists.name IN ('A', 'B')" in _sql(reading_contains_list)
    assert "lower(reading_lists.name) LIKE lower('%Road%')" in _sql(reading_contains_value)
    assert SearchService._build_collection_condition("unknown", "x") is None
    assert SearchService._build_reading_list_condition("unknown", "x") is None


def test_build_empty_condition_relationship_and_simple_paths():
    assert "NOT (EXISTS" in _sql(SearchService._build_empty_condition("character", True))
    assert "EXISTS" in _sql(SearchService._build_empty_condition("character", False))
    assert "NOT (EXISTS" in _sql(SearchService._build_empty_condition("team", True))
    assert "EXISTS" in _sql(SearchService._build_empty_condition("location", False))
    assert "NOT (EXISTS" in _sql(SearchService._build_empty_condition("collection", True))
    assert "EXISTS" in _sql(SearchService._build_empty_condition("reading_list", False))
    assert "comic_credits.role = 'writer'" in _sql(SearchService._build_empty_condition("writer", True))
    assert "comic_credits.role = 'writer'" in _sql(SearchService._build_empty_condition("writer", False))
    assert "NOT (EXISTS" in _sql(SearchService._build_empty_condition("pull_list", True))

    empty_title = _sql(SearchService._build_empty_condition("title", True))
    non_empty_title = _sql(SearchService._build_empty_condition("title", False))
    assert "comics.title IS NULL" in empty_title and "comics.title = ''" in empty_title
    assert "comics.title IS NOT NULL" in non_empty_title and "comics.title != ''" in non_empty_title

    assert SearchService._build_empty_condition("story_arc", True) is None


def test_build_pull_list_condition_scopes_to_current_user(normal_user):
    service = SearchService(MagicMock(), normal_user)

    equal_expr = service._build_pull_list_condition("equal", "Weekly")
    contains_list_expr = service._build_pull_list_condition("contains", ["A", "B"])
    contains_value_expr = service._build_pull_list_condition("contains", "Week")

    assert f"pull_lists.user_id = {normal_user.id}" in _sql(equal_expr)
    assert "pull_lists.name = 'Weekly'" in _sql(equal_expr)
    assert "pull_lists.name IN ('A', 'B')" in _sql(contains_list_expr)
    assert "lower(pull_lists.name) LIKE lower('%Week%')" in _sql(contains_value_expr)
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


@pytest.mark.parametrize(
    "sort_by,sort_order,expected_column,expects_secondary",
    [
        ("series", "desc", "series.name", False),
        ("year", "asc", "comics.year", True),
        ("title", "asc", "comics.title", False),
        ("page_count", "desc", "comics.page_count", True),
        ("rating", "desc", "comics.community_rating", True),
        ("updated", "asc", "comics.updated_at", False),
        ("created", "desc", "comics.created_at", False),
        ("unknown", "asc", "comics.created_at", False),
    ],
)
def test_apply_sorting_chooses_expected_columns(sort_by, sort_order, expected_column, expects_secondary):
    recorder = _OrderRecorder()
    result = SearchService._apply_sorting(recorder, sort_by, sort_order)

    assert result is recorder
    assert len(recorder.calls) == (2 if expects_secondary else 1)

    primary_sql = str(recorder.calls[0][0])
    assert expected_column in primary_sql
    assert (" DESC" in primary_sql) == (sort_order == "desc")

    if expects_secondary:
        secondary = [str(expr) for expr in recorder.calls[1]]
        assert secondary == ["series.name ASC", "comics.number ASC"]


def test_format_comic_uses_thumbnail_helper(monkeypatch, db):
    data = _seed_search_graph(db)
    comic = data["alpha"]
    monkeypatch.setattr("app.services.search.get_thumbnail_url", lambda comic_id, updated_at: f"/thumb/{comic_id}")

    payload = SearchService._format_comic(comic)
    assert payload["id"] == comic.id
    assert payload["series"] == data["series"].name
    assert payload["thumbnail_path"] == f"/thumb/{comic.id}"
