from unittest.mock import MagicMock

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
