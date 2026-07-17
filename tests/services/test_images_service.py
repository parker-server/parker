import zipfile
from io import BytesIO
from pathlib import Path

import app  # noqa: F401  # Ensure optional Pillow codecs register before creating fixtures.
from PIL import Image, ImageDraw

from app.services.images import ImageService


def _write_jxl_page(path: Path, accent: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (48, 72), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 47, 23), fill=accent)
    draw.rectangle((0, 24, 47, 47), fill=(32, 64, 128))
    draw.rectangle((0, 48, 47, 71), fill=(220, 80, 80))
    image.save(path, format="JXL")


def _build_jxl_cbz(tmp_path: Path) -> Path:
    first_page = tmp_path / "01_cover.jxl"
    second_page = tmp_path / "02_story.jxl"
    archive_path = tmp_path / "sample-jxl.cbz"

    _write_jxl_page(first_page, (24, 160, 96))
    _write_jxl_page(second_page, (192, 120, 32))

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(first_page, arcname=first_page.name)
        archive.write(second_page, arcname=second_page.name)

    return archive_path


def test_image_service_process_cover_and_extract_palette_from_jxl_cbz(tmp_path):
    archive_path = _build_jxl_cbz(tmp_path)
    thumbnail_path = tmp_path / "generated-cover.webp"

    service = ImageService()

    result = service.process_cover(str(archive_path), thumbnail_path)
    palette = service.extract_palette(str(archive_path))

    assert result["success"] is True
    assert thumbnail_path.exists()
    assert thumbnail_path.stat().st_size > 0
    assert result["palette"] is not None
    assert result["palette"]["primary"].startswith("#")
    assert result["palette"]["secondary"].startswith("#")

    assert palette is not None
    assert palette["primary"].startswith("#")
    assert palette["secondary"].startswith("#")


def test_image_service_get_page_image_returns_native_jxl_bytes_from_archive(tmp_path):
    archive_path = _build_jxl_cbz(tmp_path)

    service = ImageService()
    image_bytes, success, mime_type = service.get_page_image(
        str(archive_path),
        0,
        transcode_webp=False,
    )

    assert success is True
    assert mime_type == "image/jxl"
    assert image_bytes is not None
    assert len(image_bytes) > 0

    opened = Image.open(BytesIO(image_bytes))
    assert opened.size == (48, 72)
