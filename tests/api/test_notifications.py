from unittest.mock import patch

from app.config import settings

from app.models.notification import Notification
from app.models.setting import SystemSetting
from app.services.notifications.producers import WhatsNewEntry
from app.services.notifications.service import NotificationService


def _notify(db, user, group_key=None):
    NotificationService(db).notify(
        "whats_new",
        [user.id],
        data={"version": "0.1.48", "first_title": "Notifications"},
        group_key=group_key,
    )
    return db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.id.desc()).first()


def _turn_off(db):
    db.add(SystemSetting(
        key="features.notifications_enabled",
        value="false",
        category="features",
        data_type="bool",
    ))
    db.commit()


def test_unread_count_and_list(auth_client, db, normal_user, admin_user):
    _notify(db, normal_user, group_key="a")
    _notify(db, normal_user, group_key="b")
    _notify(db, admin_user)

    assert auth_client.get("/api/notifications/unread-count").json() == {"count": 2}

    response = auth_client.get("/api/notifications/", params={"size": 1})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["unread_count"] == 2
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["kind"] == "whats_new"
    assert item["icon"] == "sparkles"
    assert item["label"] == "What's new in Parker"
    assert item["title"] == "What's new in Parker 0.1.48"
    assert item["link"] == "/whats-new"
    assert item["is_read"] is False


def test_mark_read_mark_all_read_delete_and_clear(auth_client, db, normal_user):
    first = _notify(db, normal_user, group_key="a")
    second = _notify(db, normal_user, group_key="b")

    assert auth_client.post(f"/api/notifications/{first.id}/read").status_code == 200
    assert auth_client.get("/api/notifications/", params={"unread_only": True}).json()["total"] == 1

    assert auth_client.post("/api/notifications/read-all").json() == {"updated": 1}
    assert auth_client.get("/api/notifications/unread-count").json() == {"count": 0}

    assert auth_client.delete(f"/api/notifications/{second.id}").status_code == 200
    assert auth_client.delete("/api/notifications/").json() == {"deleted": 1}
    assert db.query(Notification).count() == 0


def test_cannot_touch_another_users_notification(auth_client, db, admin_user):
    row = _notify(db, admin_user)

    assert auth_client.post(f"/api/notifications/{row.id}/read").status_code == 404
    assert auth_client.delete(f"/api/notifications/{row.id}").status_code == 404
    db.refresh(row)
    assert row.read_at is None


def test_preferences_round_trip(auth_client, db, normal_user):
    response = auth_client.get("/api/notifications/preferences")
    assert response.status_code == 200
    enabled = {kind["key"]: kind["enabled"] for kind in response.json()["kinds"]}
    assert enabled == {"follow_arrivals": True, "year_in_review": True, "whats_new": True, "problem_resolved": True}

    response = auth_client.put("/api/notifications/preferences", json={"kinds": {"whats_new": False}})
    assert response.status_code == 200
    enabled = {kind["key"]: kind["enabled"] for kind in response.json()["kinds"]}
    assert enabled == {"follow_arrivals": True, "year_in_review": True, "whats_new": False, "problem_resolved": True}

    db.refresh(normal_user)
    assert normal_user.notification_preferences == {"whats_new": {"in_app": False}}


def test_preferences_rejects_unknown_kind(auth_client):
    response = auth_client.put("/api/notifications/preferences", json={"kinds": {"nope": True}})
    assert response.status_code == 400


def test_endpoints_404_when_feature_is_off(auth_client, db, normal_user):
    row = _notify(db, normal_user)
    _turn_off(db)

    assert auth_client.get("/api/notifications/unread-count").status_code == 404
    assert auth_client.get("/api/notifications/").status_code == 404
    assert auth_client.post(f"/api/notifications/{row.id}/read").status_code == 404
    assert auth_client.post("/api/notifications/read-all").status_code == 404
    assert auth_client.delete(f"/api/notifications/{row.id}").status_code == 404
    assert auth_client.delete("/api/notifications/").status_code == 404
    assert auth_client.get("/api/notifications/preferences").status_code == 404
    assert auth_client.put("/api/notifications/preferences", json={"kinds": {}}).status_code == 404
    assert auth_client.get("/api/whats-new/").status_code == 200


def test_notifications_require_login(client):
    assert client.get("/api/notifications/unread-count").status_code == 401


def _entries():
    return [
        WhatsNewEntry(version="0.1.49", audience="all", title="Unreleased"),
        WhatsNewEntry(version="0.1.48", audience="all", title="Readers", body="Body", link="/timelines"),
        WhatsNewEntry(version="0.1.48", audience="admin", title="Admins"),
    ]


def test_whats_new_hides_admin_and_unreleased_entries(auth_client):
    with patch("app.services.notifications.producers.load_whats_new_entries", return_value=_entries()), \
            patch.object(type(settings), "version", "0.1.48"):
        reader_titles = [entry["title"] for entry in auth_client.get("/api/whats-new/").json()]

    assert reader_titles == ["Readers"]


def test_whats_new_shows_admin_entries_to_admins(admin_client):
    with patch("app.services.notifications.producers.load_whats_new_entries", return_value=_entries()), \
            patch.object(type(settings), "version", "0.1.48"):
        entries = admin_client.get("/api/whats-new/").json()

    assert [entry["title"] for entry in entries] == ["Readers", "Admins"]
    assert entries[0] == {
        "version": "0.1.48",
        "title": "Readers",
        "body": "Body",
        "link": "/timelines",
        "audience": "all",
    }


def test_notifications_page_renders_and_404s_when_off(auth_client, db):
    response = auth_client.get("/user/notifications")
    assert response.status_code == 200
    assert "notificationsPage()" in response.text

    _turn_off(db)
    assert auth_client.get("/user/notifications").status_code == 404


def test_whats_new_page_renders(auth_client):
    response = auth_client.get("/whats-new")
    assert response.status_code == 200
    assert "What's New in Parker" in response.text


def test_user_settings_shows_notification_section_only_when_on(auth_client, db):
    assert 'id="notifications"' in auth_client.get("/user/settings").text

    _turn_off(db)
    assert 'id="notifications"' not in auth_client.get("/user/settings").text
