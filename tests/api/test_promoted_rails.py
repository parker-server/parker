from datetime import date, timedelta

from app.models.comic import Comic, Volume
from app.models.promoted_rail import PromotedRail
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root


def _add_comic(db, *, library_name: str, series_name: str, title: str):
    library = create_library_with_root(db, library_name, f"/tmp/{library_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        f"{series_name}-{title}.cbz",
        number="1",
        title=title,
        filename=f"{series_name}-{title}.cbz",
    )
    return library, comic


def _active_rail(*, title: str, query_config: dict) -> PromotedRail:
    return PromotedRail(
        title=title,
        query_config=query_config,
        starts_on=date.today() - timedelta(days=1),
        ends_on=date.today() + timedelta(days=1),
        enabled=True,
        display_order=3,
    )


def test_admin_can_create_and_update_promoted_rail(admin_client):
    create_response = admin_client.post(
        "/api/promoted-rails/",
        json={
            "title": "  Halloween Horrors  ",
            "starts_on": "2026-10-01",
            "ends_on": "2026-10-31",
            "display_order": 2,
            "query": {
                "match": "all",
                "filters": [{"field": "genre", "operator": "contains", "value": ["Horror"]}],
                "sort_by": "year",
                "sort_order": "desc",
            },
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["title"] == "Halloween Horrors"
    assert created["enabled"] is False
    assert created["query"]["filters"][0]["value"] == ["Horror"]

    update_response = admin_client.patch(
        f"/api/promoted-rails/{created['id']}",
        json={"enabled": True, "ends_on": None},
    )
    assert update_response.status_code == 200
    assert update_response.json()["enabled"] is True
    assert update_response.json()["ends_on"] is None

    query_response = admin_client.patch(
        f"/api/promoted-rails/{created['id']}/query",
        json={
            "query": {
                "match": "all",
                "filters": [{"field": "title", "operator": "contains", "value": "Spooky"}],
                "sort_by": "title",
                "sort_order": "asc",
            },
        },
    )
    assert query_response.status_code == 200
    assert query_response.json()["query"]["sort_by"] == "title"


def test_promoted_rail_rejects_invalid_schedule_and_viewer_specific_stack_filter(admin_client):
    invalid_schedule = admin_client.post(
        "/api/promoted-rails/",
        json={
            "title": "Backwards",
            "starts_on": "2026-10-31",
            "ends_on": "2026-10-01",
        },
    )
    assert invalid_schedule.status_code == 422

    stack_filter = admin_client.post(
        "/api/promoted-rails/",
        json={
            "title": "Personal stack",
            "starts_on": "2026-10-01",
            "query": {
                "filters": [{"field": "pull_list", "operator": "contains", "value": ["Private"]}],
            },
        },
    )
    assert stack_filter.status_code == 422
    assert "Stack" in str(stack_filter.json())


def test_home_injects_active_promoted_rail_and_runs_it_for_the_viewer(auth_client, db, normal_user):
    allowed_library, allowed_comic = _add_comic(
        db,
        library_name="promoted-allowed",
        series_name="Featured Series",
        title="Halloween Special",
    )
    _, hidden_comic = _add_comic(
        db,
        library_name="promoted-hidden",
        series_name="Featured Hidden Series",
        title="Halloween Hidden",
    )
    normal_user.accessible_libraries.append(allowed_library)
    rail = _active_rail(
        title="Halloween Horrors",
        query_config={
            "match": "all",
            "filters": [{"field": "title", "operator": "contains", "value": "Halloween"}],
            "sort_by": "title",
            "sort_order": "asc",
        },
    )
    db.add(rail)
    db.commit()
    db.refresh(rail)

    layout_response = auth_client.get("/api/home/layout")
    assert layout_response.status_code == 200
    rails = layout_response.json()["rails"]
    featured = next(entry for entry in rails if entry["key"] == f"promoted:{rail.id}")
    assert featured == {
        "key": f"promoted:{rail.id}",
        "visible": True,
        "title": "Halloween Horrors",
        "hint": "Featured by your administrator.",
        "customized": False,
        "kind": "promoted",
        "reorderable": False,
        "promoted_id": rail.id,
        "query": {
            "match": "all",
            "filters": [{"field": "title", "operator": "contains", "value": "Halloween"}],
            "sort_by": "title",
            "sort_order": "asc",
            "limit": 50,
            "offset": 0,
            "context_library_id": None,
        },
    }
    assert rails.index(featured) == next(
        index for index, entry in enumerate(rails) if entry["key"] == "following_arrivals"
    ) + 1

    items_response = auth_client.get(f"/api/home/promoted/{rail.id}/items")
    assert items_response.status_code == 200
    assert [item["id"] for item in items_response.json()] == [allowed_comic.id]
    assert hidden_comic.id not in [item["id"] for item in items_response.json()]


def test_promoted_visibility_persists_while_a_rail_is_inactive(auth_client, db, normal_user):
    rail = _active_rail(title="Future Frights", query_config={})
    db.add(rail)
    db.commit()
    db.refresh(rail)

    save_response = auth_client.put(
        "/api/home/layout",
        json={
            "rails": [],
            "promoted_rails": [{"id": rail.id, "visible": False}],
        },
    )
    assert save_response.status_code == 200
    assert next(
        entry for entry in save_response.json()["rails"] if entry["key"] == f"promoted:{rail.id}"
    )["visible"] is False

    rail.enabled = False
    db.commit()
    inactive_layout = auth_client.get("/api/home/layout")
    assert f"promoted:{rail.id}" not in [entry["key"] for entry in inactive_layout.json()["rails"]]

    rail.enabled = True
    db.commit()
    restored_layout = auth_client.get("/api/home/layout")
    assert next(
        entry for entry in restored_layout.json()["rails"] if entry["key"] == f"promoted:{rail.id}"
    )["visible"] is False
    db.refresh(normal_user)
    assert normal_user.home_rail_layout["promoted_visibility"] == {str(rail.id): False}


def test_promoted_rail_management_requires_an_admin(auth_client):
    response = auth_client.get("/api/promoted-rails/")
    assert response.status_code == 400


def test_deleting_promoted_rail_removes_saved_visibility_preferences(admin_client, db, admin_user):
    rail = _active_rail(title="Remove Me", query_config={})
    db.add(rail)
    db.commit()
    db.refresh(rail)

    admin_user.home_rail_layout = {
        "version": 2,
        "rails": [],
        "promoted_visibility": {str(rail.id): False},
    }
    db.commit()

    response = admin_client.delete(f"/api/promoted-rails/{rail.id}")
    assert response.status_code == 200

    db.refresh(admin_user)
    assert "promoted_visibility" not in admin_user.home_rail_layout


def test_admin_featured_rail_page_and_search_editor_expose_admin_routes(admin_client):
    admin_page = admin_client.get("/admin/promoted-rails")
    assert admin_page.status_code == 200
    assert "Featured Rails" in admin_page.text
    assert "/api/promoted-rails/" in admin_page.text

    editor_page = admin_client.get("/admin/promoted-rails/1/query")
    assert editor_page.status_code == 404

    create_response = admin_client.post(
        "/api/promoted-rails/",
        json={"title": "Admin Query Editor", "starts_on": "2026-10-01"},
    )
    editor_page = admin_client.get(f"/admin/promoted-rails/{create_response.json()['id']}/query")
    assert editor_page.status_code == 200
    assert "/api/promoted-rails/{rail_id}" in editor_page.text
    assert "promotedRailEditor" in editor_page.text
    assert "saveQuery" in editor_page.text
    assert "Edit Query:" in editor_page.text


def test_ordinary_search_does_not_expose_promoted_rail_routes(auth_client):
    ordinary_search = auth_client.get("/search")
    assert ordinary_search.status_code == 200
    assert "/api/promoted-rails/" not in ordinary_search.text
