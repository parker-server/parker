from app.api.deps import get_current_user
from app.core import setting_keys
from app.main import app
from app.models.comic import Volume
from app.models.problem_report import ProblemReport
from app.models.series import Series
from app.models.setting import SystemSetting
from app.models.user import User
from app.services.problem_reports import ProblemReportService
from tests.factories import create_comic, create_library_with_root


def _seed_comic(db, user=None, *, name="Saga", age_rating=None, grant_access=True):
    library = create_library_with_root(db, f"{name}-lib", f"/tmp/{name}-lib")
    series = Series(name=name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db, volume, library.active_root, f"{name}/{name} 1.cbz",
        number="1", filename=f"{name} 1.cbz", page_count=10, age_rating=age_rating,
    )
    if user is not None and grant_access:
        user.accessible_libraries.append(library)
    db.commit()
    return comic


def _turn_off(db):
    db.add(SystemSetting(
        key=setting_keys.FEATURES_PROBLEM_REPORTS_ENABLED,
        value="false",
        category="features",
        data_type="bool",
    ))
    db.commit()


def _report(auth_client, comic, category="missing_pages", **extra):
    return auth_client.post("/api/problems/", json={"comic_id": comic.id, "category": category, **extra})


def test_categories(auth_client):
    response = auth_client.get("/api/problems/categories")

    assert response.status_code == 200
    categories = {item["key"]: item for item in response.json()["categories"]}
    assert categories["missing_pages"]["label"] == "Missing pages"
    assert categories["metadata"]["uses_page"] is False
    assert categories["other"]["requires_message"] is True


def test_create_update_list_and_withdraw(auth_client, db, normal_user):
    comic = _seed_comic(db, normal_user)

    created = _report(auth_client, comic, page_index=3, message="Pages 4-5 missing")
    assert created.status_code == 200
    assert created.json()["created"] is True
    report = created.json()["report"]
    assert report["page_index"] == 3
    assert report["comic_name"] == "Saga #1"

    updated = _report(auth_client, comic, page_index=5)
    assert updated.json()["created"] is False
    assert updated.json()["report"]["id"] == report["id"]

    for_comic = auth_client.get(f"/api/problems/comic/{comic.id}").json()["items"]
    assert [item["id"] for item in for_comic] == [report["id"]]
    mine = auth_client.get("/api/problems/mine").json()["items"]
    assert [item["page_index"] for item in mine] == [5]

    assert auth_client.delete(f"/api/problems/{report['id']}").status_code == 200
    assert auth_client.delete(f"/api/problems/{report['id']}").status_code == 404
    assert auth_client.get("/api/problems/mine").json()["items"] == []


def test_create_validation_errors(auth_client, db, normal_user):
    comic = _seed_comic(db, normal_user)

    assert _report(auth_client, comic, category="nope").status_code == 422
    assert _report(auth_client, comic, category="other").status_code == 422
    assert _report(auth_client, comic, page_index=10).status_code == 422
    assert _report(auth_client, comic, message="x" * 1001).status_code == 422


def test_cannot_report_hidden_or_age_restricted_comics(auth_client, db, normal_user):
    hidden = _seed_comic(db, normal_user, name="Hidden", grant_access=False)
    mature = _seed_comic(db, normal_user, name="Mature", age_rating="Adults Only 18+")
    normal_user.max_age_rating = "Teen"
    db.commit()

    assert _report(auth_client, hidden).status_code == 404
    assert _report(auth_client, mature).status_code == 403
    assert auth_client.get(f"/api/problems/comic/{hidden.id}").status_code == 404
    assert db.query(ProblemReport).count() == 0


def test_can_report_false_is_refused(auth_client, db, normal_user):
    comic = _seed_comic(db, normal_user)
    normal_user.can_report = False
    db.commit()

    assert _report(auth_client, comic).status_code == 403


def test_setting_off_hides_reader_endpoints_but_not_admin(client, db, normal_user, admin_user):
    comic = _seed_comic(db, normal_user)
    ProblemReportService(db).submit(normal_user, comic, "broken")
    _turn_off(db)

    app.dependency_overrides[get_current_user] = lambda: normal_user
    assert client.get("/api/problems/categories").status_code == 404
    assert _report(client, comic).status_code == 404
    assert client.get("/api/problems/mine").status_code == 404
    assert client.get(f"/api/problems/comic/{comic.id}").status_code == 404

    app.dependency_overrides[get_current_user] = lambda: admin_user
    response = client.get("/api/problems/")
    assert response.status_code == 200
    assert response.json()["open_count"] == 1


def test_readers_never_see_other_readers_reports(auth_client, db, normal_user):
    comic = _seed_comic(db, normal_user)
    other = User(username="other", email="other@example.com", hashed_password="x", is_active=True)
    db.add(other)
    db.commit()
    other_report, _ = ProblemReportService(db).submit(other, comic, "broken")

    assert auth_client.get(f"/api/problems/comic/{comic.id}").json()["items"] == []
    assert auth_client.get("/api/problems/mine").json()["items"] == []
    assert auth_client.delete(f"/api/problems/{other_report.id}").status_code == 404


def test_admin_endpoints_refuse_readers(auth_client):
    # The shared admin guard answers 400 for non-admins.
    assert auth_client.get("/api/problems/").status_code == 400
    assert auth_client.get("/api/problems/summary").status_code == 400
    assert auth_client.patch("/api/problems/1", json={"action": "resolve"}).status_code == 400
    assert auth_client.post("/api/problems/batch", json={"report_ids": [1], "action": "resolve"}).status_code == 400


def test_admin_queue_resolve_reopen_and_batch(client, db, normal_user, admin_user):
    first = _seed_comic(db, normal_user, name="First")
    second = _seed_comic(db, normal_user, name="Second")
    service = ProblemReportService(db)
    report, _ = service.submit(normal_user, first, "missing_pages", page_index=2)
    other, _ = service.submit(normal_user, second, "broken")

    app.dependency_overrides[get_current_user] = lambda: admin_user
    queue = client.get("/api/problems/").json()
    assert queue["open_count"] == 2
    assert {group["comic_name"] for group in queue["groups"]} == {"First #1", "Second #1"}
    assert client.get("/api/problems/summary").json() == {"open_count": 2}

    resolved = client.patch(f"/api/problems/{report.id}", json={"action": "resolve", "note": "Fixed"})
    assert resolved.status_code == 200
    assert resolved.json()["updated"] == 1
    assert resolved.json()["reports"][0]["admin_note"] == "Fixed"
    assert resolved.json()["open_count"] == 1

    closed = client.get("/api/problems/", params={"status": "closed"}).json()["groups"]
    assert [group["status"] for group in closed] == ["resolved"]

    assert client.patch(f"/api/problems/{report.id}", json={"action": "reopen"}).json()["open_count"] == 2
    assert client.patch("/api/problems/9999", json={"action": "resolve"}).status_code == 404
    assert client.patch(f"/api/problems/{report.id}", json={"action": "explode"}).status_code == 422

    batch = client.post("/api/problems/batch", json={"report_ids": [report.id, other.id], "action": "dismiss"})
    assert batch.json()["updated"] == 2
    assert client.get("/api/problems/summary").json() == {"open_count": 0}

    app.dependency_overrides[get_current_user] = lambda: normal_user
    mine = client.get("/api/problems/mine").json()["items"]
    assert {item["status"] for item in mine} == {"dismissed"}
    assert "reporter" not in mine[0]


def test_user_admin_can_toggle_can_report(admin_client, db, normal_user):
    response = admin_client.patch(
        f"/api/users/{normal_user.id}",
        json={"email": normal_user.email, "can_report": False},
    )
    assert response.status_code == 200
    db.refresh(normal_user)
    assert normal_user.can_report is False

    listed = admin_client.get("/api/users/").json()["items"]
    assert next(user for user in listed if user["id"] == normal_user.id)["can_report"] is False


def test_comic_page_and_reader_hide_report_button_when_off(auth_client, db, normal_user):
    comic = _seed_comic(db, normal_user)

    assert "data-report-problem" in auth_client.get(f"/comics/{comic.id}").text
    assert "data-report-problem-toggle" in auth_client.get(f"/reader/{comic.id}").text

    _turn_off(db)
    assert "data-report-problem" not in auth_client.get(f"/comics/{comic.id}").text
    assert "data-report-problem-toggle" not in auth_client.get(f"/reader/{comic.id}").text


def test_my_reports_page_and_dashboard_card_follow_the_setting(auth_client, db):
    page = auth_client.get("/user/problems")
    assert page.status_code == 200
    assert "data-my-problem-reports" in page.text
    assert "data-my-problem-reports-card" in auth_client.get("/user/dashboard").text

    _turn_off(db)
    assert auth_client.get("/user/problems").status_code == 404
    assert "data-my-problem-reports-card" not in auth_client.get("/user/dashboard").text


def _assert_kept_without_comic(db, report_id, label):
    db.expire_all()
    kept = db.get(ProblemReport, report_id)
    assert kept is not None
    assert kept.comic_id is None
    assert kept.comic_label == label


def test_deleting_a_library_or_root_keeps_its_reports(admin_client, db, normal_user):
    # SQLite foreign keys are off, so these deletes must go through the ORM for
    # reports to be kept. A bulk delete would leave them pointing at nothing.
    service = ProblemReportService(db)
    in_library = _seed_comic(db, name="Library")
    library_report, _ = service.submit(normal_user, in_library, "broken")
    library_id = in_library.volume.series.library_id
    in_root = _seed_comic(db, name="Root")
    root_report, _ = service.submit(normal_user, in_root, "broken")
    root = in_root.library_root

    assert admin_client.delete(f"/api/libraries/{library_id}").status_code == 200
    _assert_kept_without_comic(db, library_report.id, library_report.comic_label)

    response = admin_client.delete(
        f"/api/libraries/{root.library_id}/roots/{root.id}", params={"delete_comics": "true"},
    )
    assert response.status_code == 200, response.text
    _assert_kept_without_comic(db, root_report.id, root_report.comic_label)

