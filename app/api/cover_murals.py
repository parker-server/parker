import re
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image, ImageOps
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.api.comics import filter_by_user_access
from app.api.deps import CurrentUser, SessionDep
from app.core.comic_helpers import get_series_age_restriction, get_thumbnail_url
from app.models.comic import Comic, Volume
from app.models.cover_mural import CoverMural, CoverMuralItem
from app.models.series import Series
from app.schemas.cover_mural import (
    BatchAddCoverMuralItemsRequest,
    COVER_MURAL_MAX_EXPORT_BLEED,
    COVER_MURAL_MAX_EXPORT_SPACING,
    COVER_MURAL_MAX_EXPORT_SCALE,
    COVER_MURAL_MAX_PIXELS,
    CoverMuralCreate,
    CoverMuralLayoutUpdate,
    CoverMuralUpdate,
)
from app.services.images import ImageService

router = APIRouter()

DEFAULT_ITEM_COLUMNS = 5


def _get_mural_or_404(mural_id: int, db: SessionDep, current_user: CurrentUser) -> CoverMural:
    mural = (
        db.query(CoverMural)
        .filter(CoverMural.id == mural_id, CoverMural.user_id == current_user.id)
        .first()
    )
    if not mural:
        raise HTTPException(status_code=404, detail="Cover mural not found")
    return mural


def _serialize_mural(mural: CoverMural, item_count: int | None = None) -> dict:
    return {
        "id": mural.id,
        "name": mural.name,
        "description": mural.description,
        "canvas_width": mural.canvas_width,
        "canvas_height": mural.canvas_height,
        "grid_size": mural.grid_size,
        "background_color": mural.background_color,
        "item_count": item_count if item_count is not None else len(mural.items or []),
        "created_at": mural.created_at,
        "updated_at": mural.updated_at,
    }


def _visible_mural_items_query(db: SessionDep, mural_id: int, current_user: CurrentUser):
    query = (
        db.query(CoverMuralItem)
        .join(Comic, CoverMuralItem.comic_id == Comic.id)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .options(
            joinedload(CoverMuralItem.comic)
            .joinedload(Comic.volume)
            .joinedload(Volume.series),
            joinedload(CoverMuralItem.comic).joinedload(Comic.library_root),
        )
        .filter(CoverMuralItem.mural_id == mural_id)
    )

    query = filter_by_user_access(query, current_user)

    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    return query.order_by(CoverMuralItem.z_index.asc(), CoverMuralItem.id.asc())


def _serialize_item(item: CoverMuralItem) -> dict:
    comic = item.comic
    series_name = comic.volume.series.name if comic and comic.volume and comic.volume.series else ""
    label = f"{series_name} #{comic.number}" if comic else ""
    return {
        "item_id": item.id,
        "comic_id": item.comic_id,
        "title": comic.title if comic else None,
        "series_name": series_name,
        "number": comic.number if comic else None,
        "label": label,
        "thumbnail_path": get_thumbnail_url(comic.id, comic.updated_at) if comic else None,
        "x": item.x,
        "y": item.y,
        "width": item.width,
        "height": item.height,
        "rotation": item.rotation,
        "z_index": item.z_index,
        "fit_mode": item.fit_mode,
    }


def _validate_canvas_size(width: int, height: int) -> None:
    if width * height > COVER_MURAL_MAX_PIXELS:
        raise HTTPException(
            status_code=422,
            detail=f"Canvas is too large. Maximum export size is {COVER_MURAL_MAX_PIXELS:,} pixels.",
        )


def _layout_slot(mural: CoverMural, index: int) -> tuple[int, int, int, int]:
    grid = max(int(mural.grid_size or 24), 4)
    width = grid * 8
    height = grid * 12
    gap = grid
    columns = max(1, min(DEFAULT_ITEM_COLUMNS, (mural.canvas_width - gap) // (width + gap)))
    x = gap + (index % columns) * (width + gap)
    y = gap + (index // columns) * (height + gap)
    return x, y, width, height


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-")
    return normalized or "cover-mural"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    clean = value.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _fit_cover_image(img: Image.Image, width: int, height: int, fit_mode: str) -> Image.Image:
    size = (max(width, 1), max(height, 1))
    source = ImageOps.exif_transpose(img).convert("RGBA")

    if fit_mode == "cover":
        return ImageOps.fit(source, size, method=Image.Resampling.LANCZOS)

    if fit_mode == "stretch":
        return source.resize(size, Image.Resampling.LANCZOS)

    fitted = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", size, (0, 0, 0, 0))
    x = (size[0] - fitted.width) // 2
    y = (size[1] - fitted.height) // 2
    tile.alpha_composite(fitted, (x, y))
    return tile


def _render_export_covers(
    items: list[CoverMuralItem],
    image_service: ImageService,
    scale: float,
    source_size: bool,
) -> list[tuple[CoverMuralItem, Image.Image]]:
    rendered_items = []
    for item in items:
        comic = item.comic
        if not comic:
            continue

        image_bytes, success, _ = image_service.get_page_image(
            comic.absolute_path,
            0,
            transcode_webp=False,
        )
        if not success or not image_bytes:
            raise HTTPException(status_code=404, detail=f"Could not read cover for {comic.title or comic.filename}")

        with Image.open(BytesIO(image_bytes)) as source:
            if source_size:
                cover = ImageOps.exif_transpose(source).convert("RGBA")
                if scale != 1:
                    cover = cover.resize(
                        (max(round(cover.width * scale), 1), max(round(cover.height * scale), 1)),
                        Image.Resampling.LANCZOS,
                    )
            else:
                cover = _fit_cover_image(
                    source,
                    round(item.width * scale),
                    round(item.height * scale),
                    item.fit_mode,
                )

        if item.rotation:
            cover = cover.rotate(float(item.rotation), resample=Image.Resampling.BICUBIC, expand=True)

        rendered_items.append((item, cover))

    return rendered_items


def _trim_transparent_edges(img: Image.Image) -> Image.Image:
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    bbox = alpha.getbbox() if alpha else None
    if not bbox:
        return img

    return img.crop(bbox)


def _preserve_canvas_placements(
    mural: CoverMural,
    rendered_items: list[tuple[CoverMuralItem, Image.Image]],
    scale: float,
) -> tuple[int, int, list[tuple[Image.Image, int, int]]]:
    placements = []
    for item, cover in rendered_items:
        item_x = round(item.x * scale)
        item_y = round(item.y * scale)
        item_width = round(item.width * scale)
        item_height = round(item.height * scale)
        paste_x = item_x + (item_width - cover.width) // 2
        paste_y = item_y + (item_height - cover.height) // 2
        placements.append((cover, paste_x, paste_y))

    return round(mural.canvas_width * scale), round(mural.canvas_height * scale), placements


def _compact_canvas_placements(
    rendered_items: list[tuple[CoverMuralItem, Image.Image]],
    spacing: int,
    horizontal_bleed: int,
    vertical_bleed: int,
    horizontal_stack: Literal["right-over-left", "left-over-right"],
    vertical_stack: Literal["lower-over-upper", "upper-over-lower"],
) -> tuple[int, int, list[tuple[Image.Image, int, int]]]:
    rendered_items = [(item, _trim_transparent_edges(cover)) for item, cover in rendered_items]
    horizontal_gap = spacing - horizontal_bleed
    vertical_gap = spacing - vertical_bleed
    rows = []
    for entry in sorted(rendered_items, key=lambda pair: (pair[0].y, pair[0].x, pair[0].z_index, pair[0].id)):
        item = entry[0]
        item_bottom = item.y + item.height
        if rows and item.y < rows[-1]["bottom"]:
            rows[-1]["entries"].append(entry)
            rows[-1]["bottom"] = max(rows[-1]["bottom"], item_bottom)
        else:
            rows.append({"bottom": item_bottom, "entries": [entry]})

    placement_records = []
    canvas_width = 1
    cursor_y = 0
    for row_index, row in enumerate(rows):
        row_entries = sorted(row["entries"], key=lambda pair: (pair[0].x, pair[0].z_index, pair[0].id))
        row_height = max(cover.height for _, cover in row_entries)
        cursor_x = 0
        row_width = 1

        for column_index, (_, cover) in enumerate(row_entries):
            placement_records.append(
                {
                    "cover": cover,
                    "x": cursor_x,
                    "y": cursor_y + (row_height - cover.height) // 2,
                    "row": row_index,
                    "column": column_index,
                }
            )
            row_width = max(row_width, cursor_x + cover.width)
            cursor_x += max(cover.width + horizontal_gap, 1)

        canvas_width = max(canvas_width, row_width)
        cursor_y += max(row_height + vertical_gap, 1)

    canvas_height = max(
        (record["y"] + record["cover"].height for record in placement_records),
        default=1,
    )
    placements = [
        (record["cover"], record["x"], record["y"])
        for record in sorted(
            placement_records,
            key=lambda record: (
                record["row"] if vertical_stack == "lower-over-upper" else -record["row"],
                record["column"] if horizontal_stack == "right-over-left" else -record["column"],
            ),
        )
    ]
    return canvas_width, canvas_height, placements


def _compose_export_image(
    width: int,
    height: int,
    placements: list[tuple[Image.Image, int, int]],
    background: tuple[int, int, int, int],
    crop_to_content: bool,
) -> Image.Image:
    transparent_canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for cover, paste_x, paste_y in placements:
        transparent_canvas.alpha_composite(cover, (paste_x, paste_y))

    if crop_to_content:
        content_bounds = transparent_canvas.getchannel("A").getbbox()
        if content_bounds:
            transparent_canvas = transparent_canvas.crop(content_bounds)

    canvas = Image.new("RGBA", transparent_canvas.size, background)
    canvas.alpha_composite(transparent_canvas)
    return canvas


@router.get("/", name="list")
def list_cover_murals(db: SessionDep, current_user: CurrentUser):
    murals = (
        db.query(CoverMural, func.count(CoverMuralItem.id).label("item_count"))
        .outerjoin(CoverMuralItem, CoverMuralItem.mural_id == CoverMural.id)
        .filter(CoverMural.user_id == current_user.id)
        .group_by(CoverMural.id)
        .order_by(CoverMural.updated_at.desc(), CoverMural.name.asc())
        .all()
    )

    return [_serialize_mural(mural, int(item_count or 0)) for mural, item_count in murals]


@router.post("/", name="create")
def create_cover_mural(
    mural_data: CoverMuralCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    _validate_canvas_size(mural_data.canvas_width, mural_data.canvas_height)

    mural = CoverMural(
        user_id=current_user.id,
        name=mural_data.name,
        description=mural_data.description,
        canvas_width=mural_data.canvas_width,
        canvas_height=mural_data.canvas_height,
        grid_size=mural_data.grid_size,
        background_color=mural_data.background_color,
    )
    db.add(mural)
    db.commit()
    db.refresh(mural)
    return _serialize_mural(mural, 0)


@router.get("/{mural_id}", name="detail")
def get_cover_mural(mural_id: int, db: SessionDep, current_user: CurrentUser):
    mural = _get_mural_or_404(mural_id, db, current_user)
    items = _visible_mural_items_query(db, mural_id, current_user).all()
    data = _serialize_mural(mural, len(items))
    data["items"] = [_serialize_item(item) for item in items]
    return data


@router.put("/{mural_id}", name="update")
def update_cover_mural(
    mural_id: int,
    update_data: CoverMuralUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    mural = _get_mural_or_404(mural_id, db, current_user)
    updated_fields = update_data.model_fields_set

    if "name" in updated_fields and update_data.name is not None:
        mural.name = update_data.name
    if "description" in updated_fields:
        mural.description = update_data.description
    if "canvas_width" in updated_fields and update_data.canvas_width is not None:
        mural.canvas_width = update_data.canvas_width
    if "canvas_height" in updated_fields and update_data.canvas_height is not None:
        mural.canvas_height = update_data.canvas_height
    if "grid_size" in updated_fields and update_data.grid_size is not None:
        mural.grid_size = update_data.grid_size
    if "background_color" in updated_fields and update_data.background_color is not None:
        mural.background_color = update_data.background_color

    _validate_canvas_size(mural.canvas_width, mural.canvas_height)

    db.commit()
    db.refresh(mural)
    return _serialize_mural(mural)


@router.delete("/{mural_id}", name="delete")
def delete_cover_mural(mural_id: int, db: SessionDep, current_user: CurrentUser):
    mural = _get_mural_or_404(mural_id, db, current_user)
    db.delete(mural)
    db.commit()
    return {"message": "Cover mural deleted"}


@router.post("/{mural_id}/items/batch", name="batch_add_items")
def batch_add_items_to_mural(
    mural_id: int,
    batch_data: BatchAddCoverMuralItemsRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    mural = _get_mural_or_404(mural_id, db, current_user)

    comic_ids = []
    seen = set()
    for comic_id in batch_data.comic_ids:
        if comic_id not in seen:
            comic_ids.append(comic_id)
            seen.add(comic_id)

    if not comic_ids:
        return {"message": "No comics selected", "added": 0}

    query = (
        db.query(Comic)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .options(joinedload(Comic.volume).joinedload(Volume.series))
        .filter(Comic.id.in_(comic_ids))
    )
    query = filter_by_user_access(query, current_user)
    age_filter = get_series_age_restriction(current_user)
    if age_filter is not None:
        query = query.filter(age_filter)

    comics_by_id = {comic.id: comic for comic in query.all()}
    ordered_comic_ids = [comic_id for comic_id in comic_ids if comic_id in comics_by_id]

    if not ordered_comic_ids:
        return {"message": "No accessible comics selected", "added": 0}

    existing_count = db.query(func.count(CoverMuralItem.id)).filter(CoverMuralItem.mural_id == mural_id).scalar() or 0
    max_z = db.query(func.max(CoverMuralItem.z_index)).filter(CoverMuralItem.mural_id == mural_id).scalar()
    next_z = (max_z if max_z is not None else -1) + 1

    new_items = []
    for offset, comic_id in enumerate(ordered_comic_ids):
        x, y, width, height = _layout_slot(mural, existing_count + offset)
        mural.canvas_height = max(mural.canvas_height, y + height + mural.grid_size)
        new_items.append(
            CoverMuralItem(
                mural_id=mural_id,
                comic_id=comic_id,
                x=x,
                y=y,
                width=width,
                height=height,
                z_index=next_z + offset,
                fit_mode="contain",
            )
        )

    _validate_canvas_size(mural.canvas_width, mural.canvas_height)

    db.add_all(new_items)
    db.commit()

    return {"message": f"Added {len(new_items)} comics to mural", "added": len(new_items)}


@router.delete("/{mural_id}/items/{item_id}", name="remove_item")
def remove_mural_item(mural_id: int, item_id: int, db: SessionDep, current_user: CurrentUser):
    _get_mural_or_404(mural_id, db, current_user)
    item = (
        db.query(CoverMuralItem)
        .filter(CoverMuralItem.mural_id == mural_id, CoverMuralItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found in mural")

    db.delete(item)
    db.commit()
    return {"message": "Item removed"}


@router.post("/{mural_id}/layout", name="save_layout")
def save_mural_layout(
    mural_id: int,
    layout_data: CoverMuralLayoutUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    mural = _get_mural_or_404(mural_id, db, current_user)
    updated_fields = layout_data.model_fields_set

    if "name" in updated_fields and layout_data.name is not None:
        mural.name = layout_data.name
    if "description" in updated_fields:
        mural.description = layout_data.description
    if "canvas_width" in updated_fields and layout_data.canvas_width is not None:
        mural.canvas_width = layout_data.canvas_width
    if "canvas_height" in updated_fields and layout_data.canvas_height is not None:
        mural.canvas_height = layout_data.canvas_height
    if "grid_size" in updated_fields and layout_data.grid_size is not None:
        mural.grid_size = layout_data.grid_size
    if "background_color" in updated_fields and layout_data.background_color is not None:
        mural.background_color = layout_data.background_color

    _validate_canvas_size(mural.canvas_width, mural.canvas_height)

    item_ids = [item.item_id for item in layout_data.items]
    item_map = {}
    if item_ids:
        items = (
            db.query(CoverMuralItem)
            .filter(CoverMuralItem.mural_id == mural_id, CoverMuralItem.id.in_(item_ids))
            .all()
        )
        item_map = {item.id: item for item in items}

    for item_data in layout_data.items:
        item = item_map.get(item_data.item_id)
        if not item:
            continue

        item.x = item_data.x
        item.y = item_data.y
        item.width = item_data.width
        item.height = item_data.height
        item.rotation = item_data.rotation
        item.z_index = item_data.z_index
        item.fit_mode = item_data.fit_mode

    db.commit()
    db.refresh(mural)
    return get_cover_mural(mural_id, db, current_user)


@router.get("/{mural_id}/export", name="export")
def export_cover_mural(
    mural_id: int,
    db: SessionDep,
    current_user: CurrentUser,
    spacing: int = Query(default=0, ge=0, le=COVER_MURAL_MAX_EXPORT_SPACING),
    bleed: int = Query(default=0, ge=0, le=COVER_MURAL_MAX_EXPORT_BLEED),
    horizontal_bleed: int | None = Query(default=None, ge=0, le=COVER_MURAL_MAX_EXPORT_BLEED),
    vertical_bleed: int | None = Query(default=None, ge=0, le=COVER_MURAL_MAX_EXPORT_BLEED),
    horizontal_stack: Literal["right-over-left", "left-over-right"] = Query(default="right-over-left"),
    vertical_stack: Literal["lower-over-upper", "upper-over-lower"] = Query(default="lower-over-upper"),
    compact: bool = Query(default=True),
    scale: float = Query(default=1, ge=1, le=COVER_MURAL_MAX_EXPORT_SCALE),
    source: bool = Query(default=True),
):
    mural = _get_mural_or_404(mural_id, db, current_user)

    items = _visible_mural_items_query(db, mural_id, current_user).all()
    if not items:
        raise HTTPException(status_code=400, detail="Mural has no exportable covers")

    background = (*_hex_to_rgb(mural.background_color), 255)
    image_service = ImageService()
    rendered_items = _render_export_covers(items, image_service, scale, source_size=compact and source)
    if not rendered_items:
        raise HTTPException(status_code=400, detail="Mural has no exportable covers")

    if compact:
        export_horizontal_bleed = bleed if horizontal_bleed is None else horizontal_bleed
        export_vertical_bleed = bleed if vertical_bleed is None else vertical_bleed
        export_width, export_height, placements = _compact_canvas_placements(
            rendered_items,
            round(spacing * scale),
            round(export_horizontal_bleed * scale),
            round(export_vertical_bleed * scale),
            horizontal_stack,
            vertical_stack,
        )
        _validate_canvas_size(export_width, export_height)
    else:
        export_width, export_height, placements = _preserve_canvas_placements(mural, rendered_items, scale)
        _validate_canvas_size(export_width, export_height)

    canvas = _compose_export_image(export_width, export_height, placements, background, crop_to_content=compact)
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG")

    filename = f"{_safe_filename(mural.name)}.png"
    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
