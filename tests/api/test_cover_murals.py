import zipfile
from io import BytesIO

from PIL import Image

from app.core.security import get_password_hash
from app.models.comic import Volume
from app.models.cover_mural import CoverMural, CoverMuralItem
from app.models.series import Series
from app.models.user import User
from app.schemas.cover_mural import COVER_MURAL_NAME_MAX_LENGTH
from tests.factories import create_comic, create_library_with_root


def _seed_comics(
    db,
    normal_user,
    tmp_path=None,
    prefix="mural",
    with_files=False,
    cover_size=(48, 72),
    cover_format="JPEG",
):
    root_path = tmp_path if tmp_path is not None else f"/tmp/{prefix}-lib"
    library = create_library_with_root(db, f"{prefix}-lib", str(root_path))
    normal_user.accessible_libraries.append(library)

    root = library.active_root
    series = Series(name=f"{prefix}-series", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    comics = []
    for number in range(1, 4):
        relative_path = f"{prefix}-{number}.cbz"
        if with_files:
            _write_cover_archive(
                tmp_path / relative_path,
                color=(40 * number, 60, 120),
                size=cover_size,
                image_format=cover_format,
            )

        comics.append(
            create_comic(
                db,
                volume,
                root,
                relative_path,
                number=str(number),
                title=f"{prefix}-{number}",
                filename=relative_path,
                page_count=1,
            )
        )

    db.commit()

    for comic in comics:
        db.refresh(comic)

    return library, comics


def _write_cover_archive(path, color=(40, 60, 120), size=(48, 72), image_format="JPEG"):
    extension = ".png" if image_format == "PNG" else ".jpg"
    image_path = path.with_suffix(extension)
    Image.new("RGB", size, color=color).save(image_path, format=image_format)
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(image_path, arcname=f"00_cover{extension}")


def test_cover_murals_create_list_and_owner_filter(auth_client, db, normal_user):
    other_user = User(
        username="mural-other",
        email="mural-other@example.com",
        hashed_password=get_password_hash("password123"),
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()
    db.add(CoverMural(user_id=other_user.id, name="Other Mural"))
    db.commit()

    create = auth_client.post(
        "/api/cover-murals/",
        json={
            "name": "  Gatefold Set  ",
            "description": "  connected covers  ",
            "canvas_width": 960,
            "canvas_height": 720,
            "background_color": "#ABCDEF",
        },
    )

    assert create.status_code == 200
    payload = create.json()
    assert payload["name"] == "Gatefold Set"
    assert payload["description"] == "connected covers"
    assert payload["canvas_width"] == 960
    assert payload["canvas_height"] == 720
    assert payload["background_color"] == "#abcdef"

    listed = auth_client.get("/api/cover-murals/")

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["Gatefold Set"]

    invalid = auth_client.post("/api/cover-murals/", json={"name": "x" * (COVER_MURAL_NAME_MAX_LENGTH + 1)})
    assert invalid.status_code == 422


def test_cover_mural_batch_add_save_layout_and_remove_item(auth_client, db, normal_user):
    _, comics = _seed_comics(db, normal_user, prefix="layout")
    mural = CoverMural(user_id=normal_user.id, name="Layout Mural", canvas_width=720, canvas_height=480)
    db.add(mural)
    db.commit()

    added = auth_client.post(
        f"/api/cover-murals/{mural.id}/items/batch",
        json={"comic_ids": [comics[0].id, comics[1].id, comics[0].id]},
    )

    assert added.status_code == 200
    assert added.json()["added"] == 2

    detail = auth_client.get(f"/api/cover-murals/{mural.id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["item_count"] == 2
    assert [item["comic_id"] for item in payload["items"]] == [comics[0].id, comics[1].id]

    first_item = payload["items"][0]
    second_item = payload["items"][1]
    saved = auth_client.post(
        f"/api/cover-murals/{mural.id}/layout",
        json={
            "name": "Saved Layout",
            "canvas_width": 800,
            "canvas_height": 600,
            "grid_size": 20,
            "background_color": "#000000",
            "items": [
                {
                    "item_id": first_item["item_id"],
                    "x": 40,
                    "y": 60,
                    "width": 220,
                    "height": 320,
                    "rotation": 5,
                    "z_index": 1,
                    "fit_mode": "cover",
                },
                {
                    "item_id": second_item["item_id"],
                    "x": 280,
                    "y": 60,
                    "width": 220,
                    "height": 320,
                    "rotation": 0,
                    "z_index": 0,
                    "fit_mode": "contain",
                },
            ],
        },
    )

    assert saved.status_code == 200
    saved_payload = saved.json()
    assert saved_payload["name"] == "Saved Layout"
    assert saved_payload["canvas_width"] == 800
    first_after_save = next(item for item in saved_payload["items"] if item["item_id"] == first_item["item_id"])
    assert first_after_save["x"] == 40
    assert first_after_save["fit_mode"] == "cover"

    removed = auth_client.delete(f"/api/cover-murals/{mural.id}/items/{first_item['item_id']}")

    assert removed.status_code == 200
    assert removed.json() == {"message": "Item removed"}
    remaining = db.query(CoverMuralItem).filter(CoverMuralItem.mural_id == mural.id).all()
    assert [item.comic_id for item in remaining] == [comics[1].id]


def test_cover_mural_export_generates_compact_png_from_original_covers(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(
        db,
        normal_user,
        tmp_path=tmp_path,
        prefix="export",
        with_files=True,
        cover_format="PNG",
    )

    mural = CoverMural(
        user_id=normal_user.id,
        name="Export Mural",
        canvas_width=240,
        canvas_height=240,
        grid_size=12,
        background_color="#111111",
    )
    db.add(mural)
    db.flush()
    db.add_all(
        [
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[0].id,
                x=12,
                y=12,
                width=96,
                height=144,
                z_index=0,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[1].id,
                x=180,
                y=12,
                width=96,
                height=144,
                z_index=1,
                fit_mode="contain",
            ),
        ]
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert 'filename="Export-Mural.png"' in response.headers["content-disposition"]

    exported = Image.open(BytesIO(response.content))
    assert exported.size == (96, 72)

    scaled_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?scale=2")
    assert scaled_response.status_code == 200
    scaled_export = Image.open(BytesIO(scaled_response.content))
    assert scaled_export.size == (192, 144)

    layout_sized_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?source=false&scale=2")
    assert layout_sized_response.status_code == 200
    layout_sized_export = Image.open(BytesIO(layout_sized_response.content))
    assert layout_sized_export.size == (384, 288)

    spaced_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?spacing=12")
    assert spaced_response.status_code == 200
    spaced_export = Image.open(BytesIO(spaced_response.content))
    assert spaced_export.size == (108, 72)

    bleed_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?bleed=2")
    assert bleed_response.status_code == 200
    bleed_export = Image.open(BytesIO(bleed_response.content))
    assert bleed_export.size == (94, 72)

    horizontal_bleed_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?horizontal_bleed=2")
    assert horizontal_bleed_response.status_code == 200
    horizontal_bleed_export = Image.open(BytesIO(horizontal_bleed_response.content))
    assert horizontal_bleed_export.size == (94, 72)

    horizontal_default_stack_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?horizontal_bleed=12")
    assert horizontal_default_stack_response.status_code == 200
    horizontal_default_stack_export = Image.open(BytesIO(horizontal_default_stack_response.content))
    assert horizontal_default_stack_export.size == (84, 72)
    assert horizontal_default_stack_export.getpixel((40, 10))[:3] == (80, 60, 120)

    horizontal_reverse_stack_response = auth_client.get(
        f"/api/cover-murals/{mural.id}/export?horizontal_bleed=12&horizontal_stack=left-over-right"
    )
    assert horizontal_reverse_stack_response.status_code == 200
    horizontal_reverse_stack_export = Image.open(BytesIO(horizontal_reverse_stack_response.content))
    assert horizontal_reverse_stack_export.size == (84, 72)
    assert horizontal_reverse_stack_export.getpixel((40, 10))[:3] == (40, 60, 120)

    spaced_bleed_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?spacing=12&bleed=2")
    assert spaced_bleed_response.status_code == 200
    spaced_bleed_export = Image.open(BytesIO(spaced_bleed_response.content))
    assert spaced_bleed_export.size == (106, 72)

    layout_spaced_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?source=false&spacing=12")
    assert layout_spaced_response.status_code == 200
    layout_spaced_export = Image.open(BytesIO(layout_spaced_response.content))
    assert layout_spaced_export.size == (204, 144)

    preserved_response = auth_client.get(f"/api/cover-murals/{mural.id}/export?compact=false")
    assert preserved_response.status_code == 200
    preserved_export = Image.open(BytesIO(preserved_response.content))
    assert preserved_export.size == (240, 240)


def test_cover_mural_compact_export_defaults_to_source_cover_size(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(
        db,
        normal_user,
        tmp_path=tmp_path,
        prefix="source-export",
        with_files=True,
        cover_size=(320, 480),
    )

    mural = CoverMural(
        user_id=normal_user.id,
        name="Source Export Mural",
        canvas_width=240,
        canvas_height=240,
        grid_size=12,
        background_color="#111111",
    )
    db.add(mural)
    db.flush()
    db.add_all(
        [
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[0].id,
                x=0,
                y=0,
                width=96,
                height=144,
                z_index=0,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[1].id,
                x=96,
                y=0,
                width=96,
                height=144,
                z_index=1,
                fit_mode="contain",
            ),
        ]
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export")

    assert response.status_code == 200
    exported = Image.open(BytesIO(response.content))
    assert exported.size == (640, 480)


def test_cover_mural_compact_export_trims_contain_padding(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(
        db,
        normal_user,
        tmp_path=tmp_path,
        prefix="trimmed-export",
        with_files=True,
        cover_format="PNG",
    )

    mural = CoverMural(
        user_id=normal_user.id,
        name="Trimmed Export Mural",
        canvas_width=240,
        canvas_height=240,
        grid_size=12,
        background_color="#111111",
    )
    db.add(mural)
    db.flush()
    db.add_all(
        [
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[0].id,
                x=0,
                y=0,
                width=100,
                height=144,
                z_index=0,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[1].id,
                x=100,
                y=0,
                width=100,
                height=144,
                z_index=1,
                fit_mode="contain",
            ),
        ]
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export?source=false")

    assert response.status_code == 200
    exported = Image.open(BytesIO(response.content))
    assert exported.size == (192, 144)
    assert exported.getpixel((95, 72))[:3] == (40, 60, 120)
    assert exported.getpixel((96, 72))[:3] == (80, 60, 120)


def test_cover_mural_compact_export_ignores_excess_canvas(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(db, normal_user, tmp_path=tmp_path, prefix="tall-export", with_files=True)

    mural = CoverMural(
        user_id=normal_user.id,
        name="Tall Canvas Export Mural",
        canvas_width=1392,
        canvas_height=1728,
        grid_size=12,
        background_color="#111111",
    )
    db.add(mural)
    db.flush()
    db.add_all(
        [
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[0].id,
                x=0,
                y=0,
                width=96,
                height=144,
                z_index=0,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[1].id,
                x=96,
                y=0,
                width=96,
                height=144,
                z_index=1,
                fit_mode="contain",
            ),
        ]
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export?compact=true")

    assert response.status_code == 200
    exported = Image.open(BytesIO(response.content))
    assert exported.size == (96, 72)


def test_cover_mural_export_groups_compact_rows(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(
        db,
        normal_user,
        tmp_path=tmp_path,
        prefix="stacked-export",
        with_files=True,
        cover_format="PNG",
    )

    mural = CoverMural(
        user_id=normal_user.id,
        name="Stacked Export Mural",
        canvas_width=500,
        canvas_height=500,
        grid_size=12,
        background_color="#111111",
    )
    db.add(mural)
    db.flush()
    db.add_all(
        [
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[0].id,
                x=12,
                y=12,
                width=96,
                height=144,
                z_index=0,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[1].id,
                x=180,
                y=12,
                width=96,
                height=144,
                z_index=1,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[2].id,
                x=12,
                y=240,
                width=96,
                height=144,
                z_index=2,
                fit_mode="contain",
            ),
        ]
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export?source=false&spacing=8")

    assert response.status_code == 200
    exported = Image.open(BytesIO(response.content))
    assert exported.size == (200, 296)

    vertical_bleed_response = auth_client.get(
        f"/api/cover-murals/{mural.id}/export?source=false&spacing=8&vertical_bleed=20"
    )
    assert vertical_bleed_response.status_code == 200
    vertical_bleed_export = Image.open(BytesIO(vertical_bleed_response.content))
    assert vertical_bleed_export.size == (200, 276)
    assert vertical_bleed_export.getpixel((20, 136))[:3] == (120, 60, 120)

    upper_stack_response = auth_client.get(
        f"/api/cover-murals/{mural.id}/export?source=false&spacing=8&vertical_bleed=20&vertical_stack=upper-over-lower"
    )
    assert upper_stack_response.status_code == 200
    upper_stack_export = Image.open(BytesIO(upper_stack_response.content))
    assert upper_stack_export.size == (200, 276)
    assert upper_stack_export.getpixel((20, 136))[:3] == (40, 60, 120)

    directional_bleed_response = auth_client.get(
        f"/api/cover-murals/{mural.id}/export?source=false&spacing=8&horizontal_bleed=4&vertical_bleed=20"
    )
    assert directional_bleed_response.status_code == 200
    directional_bleed_export = Image.open(BytesIO(directional_bleed_response.content))
    assert directional_bleed_export.size == (196, 276)


def test_cover_mural_export_handles_compact_column(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(
        db,
        normal_user,
        tmp_path=tmp_path,
        prefix="column-export",
        with_files=True,
        cover_format="PNG",
    )

    mural = CoverMural(
        user_id=normal_user.id,
        name="Column Export Mural",
        canvas_width=240,
        canvas_height=520,
        grid_size=12,
        background_color="#111111",
    )
    db.add(mural)
    db.flush()
    db.add_all(
        [
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[0].id,
                x=12,
                y=12,
                width=96,
                height=144,
                z_index=0,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[1].id,
                x=12,
                y=180,
                width=96,
                height=144,
                z_index=1,
                fit_mode="contain",
            ),
            CoverMuralItem(
                mural_id=mural.id,
                comic_id=comics[2].id,
                x=12,
                y=348,
                width=96,
                height=144,
                z_index=2,
                fit_mode="contain",
            ),
        ]
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export?source=false&spacing=8")

    assert response.status_code == 200
    exported = Image.open(BytesIO(response.content))
    assert exported.size == (96, 448)
    assert exported.getpixel((20, 20))[:3] == (40, 60, 120)
    assert exported.getpixel((20, 148))[:3] == (17, 17, 17)
    assert exported.getpixel((20, 172))[:3] == (80, 60, 120)
    assert exported.getpixel((20, 324))[:3] == (120, 60, 120)


def test_cover_mural_export_rejects_invalid_spacing(auth_client, db, normal_user, tmp_path):
    _, comics = _seed_comics(db, normal_user, tmp_path=tmp_path, prefix="spacing-export", with_files=True)

    mural = CoverMural(
        user_id=normal_user.id,
        name="Spacing Export Mural",
        canvas_width=240,
        canvas_height=240,
    )
    db.add(mural)
    db.flush()
    db.add(
        CoverMuralItem(
            mural_id=mural.id,
            comic_id=comics[0].id,
            x=12,
            y=12,
            width=96,
            height=144,
            z_index=0,
            fit_mode="contain",
        )
    )
    db.commit()

    response = auth_client.get(f"/api/cover-murals/{mural.id}/export?spacing=1001")

    assert response.status_code == 422

    invalid_scale = auth_client.get(f"/api/cover-murals/{mural.id}/export?scale=9")

    assert invalid_scale.status_code == 422

    invalid_bleed = auth_client.get(f"/api/cover-murals/{mural.id}/export?bleed=101")

    assert invalid_bleed.status_code == 422

    invalid_horizontal_bleed = auth_client.get(f"/api/cover-murals/{mural.id}/export?horizontal_bleed=101")

    assert invalid_horizontal_bleed.status_code == 422

    invalid_vertical_bleed = auth_client.get(f"/api/cover-murals/{mural.id}/export?vertical_bleed=101")

    assert invalid_vertical_bleed.status_code == 422

    invalid_horizontal_stack = auth_client.get(f"/api/cover-murals/{mural.id}/export?horizontal_stack=middle")

    assert invalid_horizontal_stack.status_code == 422

    invalid_vertical_stack = auth_client.get(f"/api/cover-murals/{mural.id}/export?vertical_stack=middle")

    assert invalid_vertical_stack.status_code == 422


def test_cover_mural_delete_removes_mural_and_items(auth_client, db, normal_user):
    _, comics = _seed_comics(db, normal_user, prefix="delete")
    mural = CoverMural(user_id=normal_user.id, name="Delete Me")
    db.add(mural)
    db.flush()
    db.add(
        CoverMuralItem(
            mural_id=mural.id,
            comic_id=comics[0].id,
            x=0,
            y=0,
            width=96,
            height=144,
            z_index=0,
        )
    )
    db.commit()

    response = auth_client.delete(f"/api/cover-murals/{mural.id}")

    assert response.status_code == 200
    assert response.json() == {"message": "Cover mural deleted"}
    assert db.query(CoverMural).filter(CoverMural.id == mural.id).first() is None
    assert db.query(CoverMuralItem).filter(CoverMuralItem.mural_id == mural.id).count() == 0


def test_cover_mural_owner_and_access_guards(auth_client, db, normal_user):
    _, comics = _seed_comics(db, normal_user, prefix="guards")
    other_user = User(
        username="mural-guard-other",
        email="mural-guard-other@example.com",
        hashed_password=get_password_hash("password123"),
        is_superuser=False,
        is_active=True,
    )
    db.add(other_user)
    db.flush()
    other_mural = CoverMural(user_id=other_user.id, name="Other")
    my_mural = CoverMural(user_id=normal_user.id, name="Mine")
    db.add_all([other_mural, my_mural])
    db.commit()

    assert auth_client.get(f"/api/cover-murals/{other_mural.id}").status_code == 404
    assert auth_client.post(
        f"/api/cover-murals/{other_mural.id}/items/batch",
        json={"comic_ids": [comics[0].id]},
    ).status_code == 404

    inaccessible = create_comic(
        db,
        comics[0].volume,
        comics[0].library_root,
        "inaccessible.cbz",
        number="99",
        title="Inaccessible",
        filename="inaccessible.cbz",
        age_rating="Mature 17+",
    )
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = True
    db.commit()

    blocked = auth_client.post(
        f"/api/cover-murals/{my_mural.id}/items/batch",
        json={"comic_ids": [inaccessible.id]},
    )

    assert blocked.status_code == 200
    assert blocked.json() == {"message": "No accessible comics selected", "added": 0}
