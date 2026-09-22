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


def _write_avif_page(path: Path, accent: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (48, 72), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 47, 23), fill=accent)
    draw.rectangle((0, 24, 47, 47), fill=(48, 84, 160))
    draw.rectangle((0, 48, 47, 71), fill=(200, 64, 112))
    image.save(path, format="AVIF")


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


def _build_avif_cbz(tmp_path: Path) -> Path:
    first_page = tmp_path / "01_cover.avif"
    second_page = tmp_path / "02_story.avif"
    archive_path = tmp_path / "sample-avif.cbz"

    _write_avif_page(first_page, (16, 148, 92))
    _write_avif_page(second_page, (176, 112, 40))

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(first_page, arcname=first_page.name)
        archive.write(second_page, arcname=second_page.name)

    return archive_path


def _image_bytes(image: Image.Image, image_format: str) -> bytes:
    output = BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def _build_cbz(tmp_path: Path, filename: str, pages: dict[str, bytes]) -> Path:
    archive_path = tmp_path / filename
    with zipfile.ZipFile(archive_path, "w") as archive:
        for page_name, page_bytes in pages.items():
            archive.writestr(page_name, page_bytes)
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


def test_image_service_process_cover_handles_missing_and_invalid_covers(tmp_path, monkeypatch):
    service = ImageService()

    missing_result = service.process_cover(str(tmp_path / "missing.cbz"), tmp_path / "missing.webp")

    assert missing_result == {"success": False, "palette": None}

    monkeypatch.setattr(
        service,
        "get_page_image",
        lambda *args, **kwargs: (b"not image bytes", True, "image/jpeg"),
    )

    invalid_result = service.process_cover(str(tmp_path / "invalid.cbz"), tmp_path / "invalid.webp")

    assert invalid_result == {"success": False, "palette": None}


def test_image_service_process_cover_converts_non_rgb_cover(tmp_path):
    archive_path = _build_cbz(
        tmp_path,
        "grayscale-cover.cbz",
        {
            "00_cover.png": _image_bytes(
                Image.new("L", (48, 72), 120),
                "PNG",
            )
        },
    )
    thumbnail_path = tmp_path / "grayscale-cover.webp"

    result = ImageService().process_cover(str(archive_path), thumbnail_path)

    assert result["success"] is True
    assert thumbnail_path.exists()
    assert result["palette"] is not None


def test_image_service_get_page_image_handles_missing_and_out_of_range_pages(tmp_path):
    archive_path = _build_cbz(
        tmp_path,
        "one-page.cbz",
        {"00_cover.jpg": _image_bytes(Image.new("RGB", (16, 16), (10, 20, 30)), "JPEG")},
    )
    service = ImageService()

    missing = service.get_page_image(str(tmp_path / "missing.cbz"), 0)
    negative = service.get_page_image(str(archive_path), -1)
    too_large = service.get_page_image(str(archive_path), 1)

    assert missing == (None, False, "application/octet-stream")
    assert negative == (None, False, "application/octet-stream")
    assert too_large == (None, False, "application/octet-stream")


def test_image_service_get_page_image_applies_grayscale_sharpen_and_webp_paths(tmp_path):
    png_archive = _build_cbz(
        tmp_path,
        "filtered.cbz",
        {"00_cover.png": _image_bytes(Image.new("P", (32, 32), 5), "PNG")},
    )
    webp_archive = _build_cbz(
        tmp_path,
        "webp.cbz",
        {"00_cover.webp": _image_bytes(Image.new("RGB", (32, 32), (80, 100, 120)), "WEBP")},
    )
    large_bmp_archive = _build_cbz(
        tmp_path,
        "large-bmp.cbz",
        {"00_cover.bmp": _image_bytes(Image.new("RGB", (2700, 100), (20, 40, 60)), "BMP")},
    )
    service = ImageService()

    filtered_bytes, filtered_success, filtered_type = service.get_page_image(
        str(png_archive),
        0,
        sharpen=True,
        grayscale=True,
    )
    webp_bytes, webp_success, webp_type = service.get_page_image(str(webp_archive), 0, sharpen=True)
    transcoded_bytes, transcoded_success, transcoded_type = service.get_page_image(
        str(large_bmp_archive),
        0,
        transcode_webp=True,
    )

    assert filtered_success is True
    assert filtered_type == "image/jpeg"
    filtered = Image.open(BytesIO(filtered_bytes))
    assert filtered.mode == "L"

    assert webp_success is True
    assert webp_type == "image/webp"
    assert Image.open(BytesIO(webp_bytes)).format == "WEBP"

    assert transcoded_success is True
    assert transcoded_type == "image/webp"
    transcoded = Image.open(BytesIO(transcoded_bytes))
    assert transcoded.format == "WEBP"
    assert transcoded.width <= 2560


def test_image_service_get_page_image_returns_original_bytes_when_processing_fails(tmp_path):
    raw_bytes = b"not really an image"
    archive_path = _build_cbz(tmp_path, "bad-image.cbz", {"00_cover.jpg": raw_bytes})

    image_bytes, success, mime_type = ImageService().get_page_image(
        str(archive_path),
        0,
        grayscale=True,
    )

    assert image_bytes == raw_bytes
    assert success is False
    assert mime_type == "image/jpeg"


def test_image_service_get_page_image_handles_archive_errors(tmp_path):
    bad_archive = tmp_path / "not-a-zip.cbz"
    bad_archive.write_bytes(b"not a zip archive")

    result = ImageService().get_page_image(str(bad_archive), 0)

    assert result == (None, False, "application/octet-stream")


def test_image_service_get_page_count_returns_counts_and_zero_for_failures(tmp_path):
    archive_path = _build_cbz(
        tmp_path,
        "pages.cbz",
        {
            "00_cover.jpg": _image_bytes(Image.new("RGB", (16, 16), (10, 20, 30)), "JPEG"),
            "01_story.png": _image_bytes(Image.new("RGB", (16, 16), (40, 50, 60)), "PNG"),
            "ComicInfo.xml": b"<ComicInfo />",
        },
    )
    bad_archive = tmp_path / "bad.cbz"
    bad_archive.write_bytes(b"not a zip archive")

    assert ImageService.get_page_count(str(archive_path)) == 2
    assert ImageService.get_page_count(str(tmp_path / "missing.cbz")) == 0
    assert ImageService.get_page_count(str(bad_archive)) == 0


def test_image_service_process_avatar_success_and_failure(tmp_path):
    service = ImageService()
    avatar_path = tmp_path / "avatars" / "me.webp"
    image_data = _image_bytes(Image.new("P", (800, 600), 3), "PNG")

    assert service.process_avatar(image_data, avatar_path) is True
    assert avatar_path.exists()
    avatar = Image.open(avatar_path)
    assert avatar.format == "WEBP"
    assert avatar.width <= service.avatar_size[0]
    assert avatar.height <= service.avatar_size[1]

    assert service.process_avatar(b"not image bytes", tmp_path / "bad.webp") is False


def test_image_service_extract_palette_returns_none_for_unavailable_or_invalid_covers(tmp_path, monkeypatch):
    service = ImageService()

    assert service.extract_palette(str(tmp_path / "missing.cbz")) is None

    archive_path = _build_cbz(
        tmp_path,
        "palette-source.cbz",
        {"00_cover.jpg": _image_bytes(Image.new("RGB", (16, 16), (10, 20, 30)), "JPEG")},
    )
    monkeypatch.setattr(service, "get_page_image", lambda *args, **kwargs: (None, False, "image/jpeg"))

    assert service.extract_palette(str(archive_path)) is None


def test_image_service_extract_palette_handles_invalid_image_bytes(tmp_path, monkeypatch):
    archive_path = _build_cbz(
        tmp_path,
        "palette-invalid.cbz",
        {"00_cover.jpg": _image_bytes(Image.new("RGB", (16, 16), (10, 20, 30)), "JPEG")},
    )
    service = ImageService()
    monkeypatch.setattr(service, "get_page_image", lambda *args, **kwargs: (b"bad", True, "image/jpeg"))

    assert service.extract_palette(str(archive_path)) is None


def test_image_service_process_cover_and_extract_palette_from_avif_cbz(tmp_path):
    archive_path = _build_avif_cbz(tmp_path)
    thumbnail_path = tmp_path / "generated-avif-cover.webp"

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


def test_image_service_get_page_image_returns_native_avif_bytes_from_archive(tmp_path):
    archive_path = _build_avif_cbz(tmp_path)

    service = ImageService()
    image_bytes, success, mime_type = service.get_page_image(
        str(archive_path),
        0,
        transcode_webp=False,
    )

    assert success is True
    assert mime_type == "image/avif"
    assert image_bytes is not None
    assert len(image_bytes) > 0

    opened = Image.open(BytesIO(image_bytes))
    assert opened.size == (48, 72)
