import json
from datetime import datetime, timedelta, timezone

from app.models.saved_search import SavedSearch
from app.models.user import User


def _create_other_user(db):
    user = User(
        username="saved-search-other",
        email="saved-search-other@example.com",
        hashed_password="x",
        is_superuser=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _payload(name: str = "My Search"):
    return {
        "name": name,
        "query": {
            "match": "all",
            "filters": [
                {
                    "field": "title",
                    "operator": "contains",
                    "value": "Batman",
                }
            ],
            "sort_by": "created",
            "sort_order": "desc",
            "limit": 25,
            "offset": 0,
        },
    }


def test_list_saved_searches_only_returns_current_user_in_desc_order(auth_client, db, normal_user):
    other_user = _create_other_user(db)

    older = SavedSearch(
        user_id=normal_user.id,
        name="Older Search",
        query_json=json.dumps(_payload("Older Search")["query"]),
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    newer = SavedSearch(
        user_id=normal_user.id,
        name="Newer Search",
        query_json=json.dumps(_payload("Newer Search")["query"]),
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    foreign = SavedSearch(
        user_id=other_user.id,
        name="Foreign Search",
        query_json=json.dumps(_payload("Foreign Search")["query"]),
        created_at=datetime.now(timezone.utc),
    )
    db.add_all([older, newer, foreign])
    db.commit()

    response = auth_client.get("/api/saved-searches/")

    assert response.status_code == 200
    payload = response.json()
    assert [row["name"] for row in payload] == ["Newer Search", "Older Search"]
    assert payload[0]["query"]["filters"][0]["value"] == "Batman"


def test_create_saved_search_persists_record(auth_client, db, normal_user):
    response = auth_client.post("/api/saved-searches/", json=_payload("Created Search"))

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Created Search"
    assert body["query"]["filters"][0]["field"] == "title"

    row = db.query(SavedSearch).filter(SavedSearch.id == body["id"]).first()
    assert row is not None
    assert row.user_id == normal_user.id
    assert json.loads(row.query_json)["sort_by"] == "created"


def test_create_saved_search_rejects_invalid_query(auth_client):
    bad_payload = _payload("Bad Search")
    bad_payload["query"]["filters"][0]["field"] = "not_a_valid_field"

    response = auth_client.post("/api/saved-searches/", json=bad_payload)

    assert response.status_code == 422


def test_delete_saved_search_success(auth_client, db, normal_user):
    row = SavedSearch(
        user_id=normal_user.id,
        name="Delete Me",
        query_json=json.dumps(_payload("Delete Me")["query"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    response = auth_client.delete(f"/api/saved-searches/{row.id}")

    assert response.status_code == 200
    assert response.json() == {"message": "Deleted"}
    assert db.query(SavedSearch).filter(SavedSearch.id == row.id).first() is None


def test_delete_saved_search_404_for_missing_or_foreign(auth_client, db, normal_user):
    other_user = _create_other_user(db)
    foreign = SavedSearch(
        user_id=other_user.id,
        name="Foreign",
        query_json=json.dumps(_payload("Foreign")["query"]),
    )
    db.add(foreign)
    db.commit()
    db.refresh(foreign)

    foreign_delete = auth_client.delete(f"/api/saved-searches/{foreign.id}")
    assert foreign_delete.status_code == 404
    assert foreign_delete.json() == {"detail": "Saved search not found"}

    missing_delete = auth_client.delete("/api/saved-searches/999999")
    assert missing_delete.status_code == 404
    assert missing_delete.json() == {"detail": "Saved search not found"}
