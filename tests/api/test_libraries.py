from unittest.mock import patch

from app.main import app
from app.api.deps import get_current_user
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.series import Series


def _create_library_series_fixture(db, *, lib_name: str):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series_alpha = Series(name="The Alpha", library=library)
    series_beta = Series(name="Beta", library=library)
    series_reverse = Series(name="Countdown", library=library)

    vol_alpha = Volume(series=series_alpha, volume_number=1)
    vol_beta = Volume(series=series_beta, volume_number=1)
    vol_reverse = Volume(series=series_reverse, volume_number=1)

    db.add_all([library, series_alpha, series_beta, series_reverse, vol_alpha, vol_beta, vol_reverse])
    db.flush()

    alpha_two = Comic(
        volume_id=vol_alpha.id,
        number="2",
        title="The Alpha #2",
        year=2002,
        filename="alpha-2.cbz",
        file_path=f"/tmp/{lib_name}-alpha-2.cbz",
        page_count=20,
    )
    alpha_one = Comic(
        volume_id=vol_alpha.id,
        number="1",
        title="The Alpha #1",
        year=2001,
        filename="alpha-1.cbz",
        file_path=f"/tmp/{lib_name}-alpha-1.cbz",
        page_count=20,
    )

    beta_five = Comic(
        volume_id=vol_beta.id,
        number="5",
        title="Beta Annual",
        year=2005,
        format="annual",
        filename="beta-5.cbz",
        file_path=f"/tmp/{lib_name}-beta-5.cbz",
        page_count=20,
    )
    beta_three = Comic(
        volume_id=vol_beta.id,
        number="3",
        title="Beta Special",
        year=2003,
        format="one-shot",
        filename="beta-3.cbz",
        file_path=f"/tmp/{lib_name}-beta-3.cbz",
        page_count=20,
    )

    reverse_one = Comic(
        volume_id=vol_reverse.id,
        number="1",
        title="Countdown #1",
        year=2001,
        filename="countdown-1.cbz",
        file_path=f"/tmp/{lib_name}-countdown-1.cbz",
        page_count=20,
    )
    reverse_four = Comic(
        volume_id=vol_reverse.id,
        number="4",
        title="Countdown #4",
        year=2004,
        filename="countdown-4.cbz",
        file_path=f"/tmp/{lib_name}-countdown-4.cbz",
        page_count=20,
    )

    db.add_all([alpha_two, alpha_one, beta_five, beta_three, reverse_one, reverse_four])
    db.commit()

    return {
        "library": library,
        "series_alpha": series_alpha,
        "series_beta": series_beta,
        "series_reverse": series_reverse,
        "alpha_one": alpha_one,
        "beta_three": beta_three,
        "reverse_four": reverse_four,
        "alpha_comics": [alpha_two, alpha_one],
    }


def test_admin_can_create_library(admin_client, db):
    payload = {"name": "Marvel Comics", "path": "/data/marvel"}

    response = admin_client.post("/api/libraries/", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Marvel Comics"
    assert data["id"] is not None

    lib = db.query(Library).first()
    assert lib.name == "Marvel Comics"


def test_create_library_duplicate_name_returns_400(admin_client, db):
    db.add(Library(name="Duplicate", path="/tmp/dup"))
    db.commit()

    response = admin_client.post("/api/libraries/", json={"name": "Duplicate", "path": "/tmp/other"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Library name already exists"}


def test_user_rls_security(client, db, admin_user, normal_user):
    lib = Library(name="Secret Library", path="/tmp")
    db.add(lib)
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: admin_user

    resp_admin = client.get("/api/libraries/")
    assert resp_admin.status_code == 200
    assert len(resp_admin.json()) == 1

    app.dependency_overrides[get_current_user] = lambda: normal_user

    resp_user = client.get("/api/libraries/")
    assert resp_user.status_code == 200
    assert len(resp_user.json()) == 0


def test_get_library_detail_requires_access(auth_client, db):
    library = Library(name="Hidden", path="/tmp/hidden")
    db.add(library)
    db.commit()

    response = auth_client.get(f"/api/libraries/{library.id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Library not found"}


def test_get_library_detail_as_admin(admin_client, db):
    library = Library(name="Visible", path="/tmp/visible")
    db.add(library)
    db.commit()

    response = admin_client.get(f"/api/libraries/{library.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == library.id
    assert payload["name"] == "Visible"


def test_get_library_series_sorts_and_computes_cover_and_read_state(auth_client, db, normal_user):
    data = _create_library_series_fixture(db, lib_name="series-fixture")
    normal_user.accessible_libraries.append(data["library"])
    db.commit()

    db.add_all([
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["alpha_comics"][0].id,
            current_page=20,
            total_pages=20,
            completed=True,
        ),
        ReadingProgress(
            user_id=normal_user.id,
            comic_id=data["alpha_comics"][1].id,
            current_page=20,
            total_pages=20,
            completed=True,
        ),
    ])
    db.commit()

    response = auth_client.get(f"/api/libraries/{data['library'].id}/series?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 3
    assert payload["page"] == 1
    assert payload["size"] == 10

    items = payload["items"]
    assert [item["id"] for item in items] == [
        data["series_alpha"].id,
        data["series_beta"].id,
        data["series_reverse"].id,
    ]
    assert [item["start_year"] for item in items] == [2001, 2003, 2004]
    assert [item["read"] for item in items] == [True, False, False]

    assert items[0]["thumbnail_path"].startswith(f"/api/comics/{data['alpha_one'].id}/thumbnail?v=")
    assert items[1]["thumbnail_path"].startswith(f"/api/comics/{data['beta_three'].id}/thumbnail?v=")
    assert items[2]["thumbnail_path"].startswith(f"/api/comics/{data['reverse_four'].id}/thumbnail?v=")


def test_get_library_series_empty_page_returns_empty_items(auth_client, db, normal_user):
    library = Library(name="No-Series", path="/tmp/no-series")
    db.add(library)
    db.commit()

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/libraries/{library.id}/series?page=1&size=5")

    assert response.status_code == 200
    assert response.json() == {"total": 0, "page": 1, "size": 5, "items": []}


def test_update_library_applies_fields_and_refreshes_watches(admin_client, db):
    library = Library(name="UpdateMe", path="/tmp/update-me", watch_mode=False)
    db.add(library)
    db.commit()

    with patch("app.api.libraries.library_watcher.refresh_watches") as mock_refresh:
        response = admin_client.patch(
            f"/api/libraries/{library.id}",
            json={"name": "Updated", "path": "/tmp/updated", "watch_mode": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Updated"
    assert payload["path"] == "/tmp/updated"
    assert payload["watch_mode"] is True
    mock_refresh.assert_called_once()


def test_update_library_rejects_duplicate_name(admin_client, db):
    original = Library(name="Original", path="/tmp/original")
    duplicate = Library(name="Taken", path="/tmp/taken")
    db.add_all([original, duplicate])
    db.commit()

    response = admin_client.patch(f"/api/libraries/{original.id}", json={"name": "Taken"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Library name already exists"}


def test_update_library_returns_404_for_missing_library(admin_client):
    response = admin_client.patch("/api/libraries/999999", json={"name": "Nope"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Library not found"}


def test_delete_library_success_and_not_found(admin_client, db):
    library = Library(name="DeleteMe", path="/tmp/delete-me")
    db.add(library)
    db.commit()

    success = admin_client.delete(f"/api/libraries/{library.id}")
    assert success.status_code == 200
    assert success.json() == {"message": "Library deleted"}
    assert db.query(Library).filter(Library.id == library.id).first() is None

    missing = admin_client.delete("/api/libraries/999999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Library not found"}


def test_scan_library_passes_force_flag_to_scan_manager(admin_client, db):
    library = Library(name="ScanMe", path="/tmp/scan-me")
    db.add(library)
    db.commit()

    expected = {"status": "queued", "job_id": 55, "message": "Queued"}
    with patch("app.api.libraries.scan_manager.add_task", return_value=expected) as mock_add_task:
        response = admin_client.post(f"/api/libraries/{library.id}/scan?force=true")

    assert response.status_code == 200
    assert response.json() == expected
    mock_add_task.assert_called_once_with(library.id, force=True)


def test_scan_library_returns_404_for_missing_library(admin_client):
    response = admin_client.post("/api/libraries/999999/scan")

    assert response.status_code == 404
    assert response.json() == {"detail": "Library not found"}


def test_has_library_access_helper_paths(db, normal_user, admin_user):
    from app.api.libraries import _has_library_access

    allowed_library = Library(name="Helper Allowed", path="/tmp/helper-allowed")
    denied_library = Library(name="Helper Denied", path="/tmp/helper-denied")
    db.add_all([allowed_library, denied_library])
    db.commit()

    normal_user.accessible_libraries.append(allowed_library)
    db.commit()

    assert _has_library_access(allowed_library.id, normal_user) is True
    assert _has_library_access(denied_library.id, normal_user) is False
    assert _has_library_access(denied_library.id, admin_user) is True


def test_list_libraries_applies_limit_for_superuser(admin_client, db):
    db.add_all([
        Library(name="A Library", path="/tmp/a"),
        Library(name="B Library", path="/tmp/b"),
        Library(name="C Library", path="/tmp/c"),
    ])
    db.commit()

    response = admin_client.get("/api/libraries/?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert [row["name"] for row in payload] == ["A Library", "B Library"]


def test_list_libraries_applies_limit_for_normal_user(auth_client, db, normal_user):
    lib_a = Library(name="Limit User A", path="/tmp/limit-user-a")
    lib_b = Library(name="Limit User B", path="/tmp/limit-user-b")
    lib_c = Library(name="Limit User C", path="/tmp/limit-user-c")
    db.add_all([lib_a, lib_b, lib_c])
    db.commit()

    normal_user.accessible_libraries.extend([lib_b, lib_a, lib_c])
    db.commit()

    response = auth_client.get("/api/libraries/?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert [row["name"] for row in payload] == ["Limit User A", "Limit User B"]


def test_list_libraries_filters_stats_by_age_restriction(auth_client, db, normal_user):
    library = Library(name="Age Stats Library", path="/tmp/age-stats-library")

    safe_series = Series(name="Safe Stats Series", library=library)
    safe_volume = Volume(series=safe_series, volume_number=1)
    safe_comic = Comic(
        volume=safe_volume,
        number="1",
        title="Safe Stats Comic",
        age_rating="Teen",
        filename="safe-stats.cbz",
        file_path="/tmp/safe-stats.cbz",
    )

    banned_series = Series(name="Banned Stats Series", library=library)
    banned_volume = Volume(series=banned_series, volume_number=1)
    banned_comic = Comic(
        volume=banned_volume,
        number="1",
        title="Banned Stats Comic",
        age_rating="Mature 17+",
        filename="banned-stats.cbz",
        file_path="/tmp/banned-stats.cbz",
    )

    db.add_all([library, safe_series, safe_volume, safe_comic, banned_series, banned_volume, banned_comic])
    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get("/api/libraries/")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["stats"] == {"series": 1, "issues": 1}


def test_get_library_series_filters_by_age_restriction(auth_client, db, normal_user):
    library = Library(name="Age Series Library", path="/tmp/age-series-library")

    safe_series = Series(name="Safe Series", library=library)
    safe_volume = Volume(series=safe_series, volume_number=1)
    safe_comic = Comic(
        volume=safe_volume,
        number="1",
        title="Safe Comic",
        age_rating="Teen",
        filename="safe-series.cbz",
        file_path="/tmp/safe-series.cbz",
    )

    banned_series = Series(name="Banned Series", library=library)
    banned_volume = Volume(series=banned_series, volume_number=1)
    banned_comic = Comic(
        volume=banned_volume,
        number="1",
        title="Banned Comic",
        age_rating="Mature 17+",
        filename="banned-series.cbz",
        file_path="/tmp/banned-series.cbz",
    )

    db.add_all([library, safe_series, safe_volume, safe_comic, banned_series, banned_volume, banned_comic])
    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.get(f"/api/libraries/{library.id}/series?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == safe_series.id


def test_get_library_series_cover_fallback_handles_non_numeric_numbers(auth_client, db, normal_user):
    library = Library(name="Library Non Numeric", path="/tmp/library-non-numeric")
    series = Series(name="Non Numeric Series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([library, series, volume])
    db.flush()

    comic_alpha = Comic(
        volume_id=volume.id,
        number="A",
        title="Non Numeric A",
        year=2024,
        filename="non-numeric-a.cbz",
        file_path="/tmp/non-numeric-a.cbz",
    )
    comic_two = Comic(
        volume_id=volume.id,
        number="2",
        title="Non Numeric #2",
        year=2023,
        filename="non-numeric-2.cbz",
        file_path="/tmp/non-numeric-2.cbz",
    )
    db.add_all([comic_alpha, comic_two])

    normal_user.accessible_libraries.append(library)
    db.commit()

    response = auth_client.get(f"/api/libraries/{library.id}/series?page=1&size=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["start_year"] == 2023


def test_create_library_with_watch_mode_refreshes_watcher(admin_client):
    with patch("app.api.libraries.library_watcher.refresh_watches") as mock_refresh:
        response = admin_client.post(
            "/api/libraries/",
            json={"name": "Watched Library", "path": "/tmp/watched-library", "watch_mode": True},
        )

    assert response.status_code == 200
    assert response.json()["watch_mode"] is True
    mock_refresh.assert_called_once()
