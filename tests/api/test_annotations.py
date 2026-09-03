from app.models.annotation import Annotation
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.user import User
from tests.factories import create_library_with_root


def _seed_annotation_data(db, normal_user, *, lib_name: str = "annotation-lib", series_name: str = "Annotation Series"):
    library = create_library_with_root(db, lib_name, f"/tmp/{lib_name}")
    root = library.active_root
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title=f"{series_name} #1",
        filename=f"{series_name}-1.cbz",
        library_root_id=root.id,
        relative_path=f"{series_name}-1.cbz",
        page_count=10,
    )

    db.add_all([series, volume, comic])
    db.flush()

    normal_user.accessible_libraries.append(library)
    db.commit()

    return {
        "library": library,
        "series": series,
        "volume": volume,
        "comic": comic,
    }


def test_get_comic_annotations_returns_sorted_current_user_items(auth_client, db, normal_user):
    data = _seed_annotation_data(db, normal_user, lib_name="annotation-list", series_name="Annotation List")

    other_user = User(
        username="annotation-other",
        email="annotation-other@example.com",
        hashed_password="fakehash",
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()

    db.add_all([
        Annotation(
            user_id=normal_user.id,
            comic_id=data["comic"].id,
            page_index=7,
            kind="pin",
            title="Later",
            color="#facc15",
            anchor_json={"x": 0.5, "y": 0.5},
        ),
        Annotation(
            user_id=normal_user.id,
            comic_id=data["comic"].id,
            page_index=2,
            kind="rectangle",
            title="Early",
            color="#38bdf8",
            anchor_json={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.2},
        ),
        Annotation(
            user_id=other_user.id,
            comic_id=data["comic"].id,
            page_index=1,
            kind="pin",
            title="Other User",
            color="#facc15",
            anchor_json={"x": 0.5, "y": 0.5},
        ),
    ])
    db.commit()

    response = auth_client.get(f"/api/annotations/comic/{data['comic'].id}")

    assert response.status_code == 200
    payload = response.json()
    assert [item["page_index"] for item in payload] == [2, 7]
    assert [item["title"] for item in payload] == ["Early", "Later"]
    assert payload[0]["anchor"] == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.2}


def test_create_annotation_persists_pin_and_rectangle(auth_client, db, normal_user):
    data = _seed_annotation_data(db, normal_user, lib_name="annotation-save", series_name="Annotation Save")

    pin_response = auth_client.post(
        f"/api/annotations/comic/{data['comic'].id}",
        json={
            "page_index": 4,
            "kind": "pin",
            "title": "Splash",
            "body": "Nice establishing panel",
            "color": "#FACC15",
            "anchor": {"x": 0.42, "y": 0.25},
        },
    )

    assert pin_response.status_code == 200
    pin_payload = pin_response.json()
    assert pin_payload["kind"] == "pin"
    assert pin_payload["title"] == "Splash"
    assert pin_payload["body"] == "Nice establishing panel"
    assert pin_payload["color"] == "#facc15"
    assert pin_payload["anchor"] == {"x": 0.42, "y": 0.25}

    rectangle_response = auth_client.post(
        f"/api/annotations/comic/{data['comic'].id}",
        json={
            "page_index": 4,
            "kind": "rectangle",
            "title": "Panel",
            "color": "#38bdf8",
            "anchor": {"x": 0.1, "y": 0.15, "width": 0.4, "height": 0.2},
        },
    )

    assert rectangle_response.status_code == 200
    assert rectangle_response.json()["anchor"] == {"x": 0.1, "y": 0.15, "width": 0.4, "height": 0.2}

    annotations = db.query(Annotation).filter(Annotation.user_id == normal_user.id).all()
    assert len(annotations) == 2


def test_create_annotation_rejects_out_of_range_page_and_invalid_anchor(auth_client, db, normal_user):
    data = _seed_annotation_data(db, normal_user, lib_name="annotation-range", series_name="Annotation Range")

    out_of_range = auth_client.post(
        f"/api/annotations/comic/{data['comic'].id}",
        json={
            "page_index": 99,
            "kind": "pin",
            "title": "Too Far",
            "anchor": {"x": 0.5, "y": 0.5},
        },
    )

    assert out_of_range.status_code == 422
    assert out_of_range.json() == {"detail": "Annotation page is out of range"}

    invalid_anchor = auth_client.post(
        f"/api/annotations/comic/{data['comic'].id}",
        json={
            "page_index": 1,
            "kind": "rectangle",
            "title": "Bad Box",
            "anchor": {"x": 0.9, "y": 0.9, "width": 0.3, "height": 0.2},
        },
    )

    assert invalid_anchor.status_code == 422


def test_create_annotation_restricted_comic_returns_403(auth_client, db, normal_user):
    data = _seed_annotation_data(db, normal_user, lib_name="annotation-restricted", series_name="Annotation Restricted")

    data["comic"].age_rating = "Mature 17+"
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    response = auth_client.post(
        f"/api/annotations/comic/{data['comic'].id}",
        json={
            "page_index": 1,
            "kind": "pin",
            "title": "Blocked",
            "anchor": {"x": 0.5, "y": 0.5},
        },
    )

    assert response.status_code == 403
    assert "restricted" in response.json()["detail"].lower()


def test_update_and_delete_annotation(auth_client, db, normal_user):
    data = _seed_annotation_data(db, normal_user, lib_name="annotation-mutate", series_name="Annotation Mutate")

    annotation = Annotation(
        user_id=normal_user.id,
        comic_id=data["comic"].id,
        page_index=3,
        kind="pin",
        title="Original",
        body="First note",
        color="#facc15",
        anchor_json={"x": 0.2, "y": 0.3},
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)

    patch_response = auth_client.patch(
        f"/api/annotations/{annotation.id}",
        json={
            "page_index": 4,
            "kind": "rectangle",
            "title": "Updated",
            "body": "",
            "color": "#38bdf8",
            "anchor": {"x": 0.2, "y": 0.25, "width": 0.35, "height": 0.2},
        },
    )

    assert patch_response.status_code == 200
    patch_payload = patch_response.json()
    assert patch_payload["page_index"] == 4
    assert patch_payload["kind"] == "rectangle"
    assert patch_payload["title"] == "Updated"
    assert patch_payload["body"] is None
    assert patch_payload["anchor"] == {"x": 0.2, "y": 0.25, "width": 0.35, "height": 0.2}

    move_response = auth_client.patch(
        f"/api/annotations/{annotation.id}",
        json={
            "anchor": {"x": 0.4, "y": 0.3, "width": 0.35, "height": 0.2},
        },
    )

    assert move_response.status_code == 200
    move_payload = move_response.json()
    assert move_payload["kind"] == "rectangle"
    assert move_payload["anchor"] == {"x": 0.4, "y": 0.3, "width": 0.35, "height": 0.2}

    delete_response = auth_client.delete(f"/api/annotations/{annotation.id}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"annotation_id": annotation.id, "message": "Annotation deleted"}
    assert db.query(Annotation).filter(Annotation.id == annotation.id).first() is None
