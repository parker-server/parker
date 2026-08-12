from app.core.security import get_password_hash
from app.models.smart_list import SmartList
from app.models.user import User
from app.services.search import SearchService


def _query_payload(**overrides):
    payload = {
        "match": "all",
        "filters": [],
        "sort_by": "created",
        "sort_order": "desc",
        "limit": 50,
        "offset": 0,
    }
    payload.update(overrides)
    return payload


def test_create_smart_list_persists_query_for_current_user(auth_client, db, normal_user):
    response = auth_client.post(
        "/api/smart-lists/",
        json={"name": "Recent Additions", "query": _query_payload(limit=15)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Recent Additions"
    assert payload["user_id"] == normal_user.id
    assert payload["query_config"]["limit"] == 15

    saved = db.query(SmartList).filter(SmartList.id == payload["id"]).first()
    assert saved is not None
    assert saved.user_id == normal_user.id
    assert saved.query_config["limit"] == 15


def test_list_smart_lists_orders_by_name_and_filters_to_owner(auth_client, db, normal_user):
    other_user = User(
        username="smartlist-other",
        email="smartlist-other@example.com",
        hashed_password=get_password_hash("test1234"),
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.commit()

    mine_z = SmartList(user_id=normal_user.id, name="Zeta", query_config=_query_payload())
    mine_a = SmartList(user_id=normal_user.id, name="Alpha", query_config=_query_payload(match="any"))
    not_mine = SmartList(user_id=other_user.id, name="Hidden", query_config=_query_payload())
    db.add_all([mine_z, mine_a, not_mine])
    db.commit()

    response = auth_client.get("/api/smart-lists/")

    assert response.status_code == 200
    items = response.json()
    assert [item["name"] for item in items] == ["Alpha", "Zeta"]
    assert all(item["query"]["sort_by"] == "created" for item in items)


def test_execute_smart_list_runs_search_with_limit_override(auth_client, db, normal_user, monkeypatch):
    smart_list = SmartList(
        user_id=normal_user.id,
        name="Dashboard Rail",
        query_config=_query_payload(limit=999, offset=123),
    )
    db.add(smart_list)
    db.commit()

    captured = {}

    def fake_search(self, request):
        captured["limit"] = request.limit
        captured["offset"] = request.offset
        captured["sort_by"] = request.sort_by
        return {
            "results": [{"id": 7, "title": "Result"}],
            "total": 1,
            "limit": request.limit,
            "offset": request.offset,
        }

    monkeypatch.setattr(SearchService, "search", fake_search)

    response = auth_client.get(f"/api/smart-lists/{smart_list.id}/items?limit=7")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == smart_list.id
    assert payload["name"] == "Dashboard Rail"
    assert payload["items"] == [{"id": 7, "title": "Result"}]

    assert captured["limit"] == 7
    assert captured["offset"] == 0
    assert captured["sort_by"] == "created"


def test_execute_smart_list_404_when_missing(auth_client):
    response = auth_client.get("/api/smart-lists/999999/items")

    assert response.status_code == 404
    assert response.json()["detail"] == "List not found"


def test_update_smart_list_updates_fields_and_query(auth_client, db, normal_user):
    smart_list = SmartList(
        user_id=normal_user.id,
        name="To Update",
        icon="old",
        show_on_dashboard=True,
        show_in_library=True,
        query_config=_query_payload(limit=5),
    )
    db.add(smart_list)
    db.commit()

    response = auth_client.patch(
        f"/api/smart-lists/{smart_list.id}",
        json={
            "name": "Updated",
            "icon": "new",
            "show_on_dashboard": False,
            "show_in_library": False,
            "query": _query_payload(match="any", limit=25),
        },
    )

    assert response.status_code == 200

    db.refresh(smart_list)
    assert smart_list.name == "Updated"
    assert smart_list.icon == "new"
    assert smart_list.show_on_dashboard is False
    assert smart_list.show_in_library is False
    assert smart_list.query_config["match"] == "any"
    assert smart_list.query_config["limit"] == 25


def test_update_smart_list_404_when_missing(auth_client):
    response = auth_client.patch("/api/smart-lists/999999", json={"name": "x"})

    assert response.status_code == 404
    assert response.json()["detail"] == "List not found"


def test_delete_smart_list_success_and_missing(auth_client, db, normal_user):
    smart_list = SmartList(
        user_id=normal_user.id,
        name="Delete Me",
        query_config=_query_payload(),
    )
    db.add(smart_list)
    db.commit()

    deleted = auth_client.delete(f"/api/smart-lists/{smart_list.id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"message": "Deleted"}

    missing = auth_client.delete(f"/api/smart-lists/{smart_list.id}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "List not found"
