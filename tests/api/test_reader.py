from unittest.mock import patch

from app.api.reader import natural_sort_key
from app.models.collection import Collection, CollectionItem
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.pull_list import PullList, PullListItem
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.series import Series


def _create_graph(db, *, lib_name: str, series_name: str, volume_number: int = 1):
    library = Library(name=lib_name, path=f"/tmp/{lib_name}")
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=volume_number)
    db.add_all([library, series, volume])
    db.flush()
    return library, series, volume


def _add_comic(db, volume: Volume, *, number: str, title: str, **kwargs):
    comic = Comic(
        volume_id=volume.id,
        number=number,
        title=title,
        filename=f"{title.replace(' ', '-')}.cbz",
        file_path=f"/tmp/{title.replace(' ', '-')}-{volume.id}-{number}.cbz",
        **kwargs,
    )
    db.add(comic)
    db.flush()
    return comic


def test_reader_natural_sort_key():
    values = ["10", "2", "10a", "1"]
    assert sorted(values, key=natural_sort_key) == ["1", "2", "10", "10a"]


def test_reader_init_default_volume_reverse_and_page_count_fallback(auth_client, db, normal_user):
    library, _, volume = _create_graph(
        db,
        lib_name="reader-default-lib",
        series_name="Countdown",
    )

    c1 = _add_comic(db, volume, number="1", title="Countdown One", page_count=0)
    c2 = _add_comic(db, volume, number="2", title="Countdown Two", page_count=0)
    c3 = _add_comic(db, volume, number="3", title="Countdown Three", page_count=0)

    normal_user.accessible_libraries.append(library)
    db.commit()

    with patch("app.api.reader.ImageService.get_page_count", return_value=77):
        response = auth_client.get(f"/api/reader/{c2.id}/read-init")

    assert response.status_code == 200
    payload = response.json()
    assert payload["comic_id"] == c2.id
    assert payload["prev_comic_id"] == c3.id
    assert payload["next_comic_id"] == c1.id
    assert payload["page_count"] == 77
    assert payload["context_type"] == "volume"
    assert payload["context_total"] == 3
    assert payload["context_position"] == 2
    assert payload["context_label"] == "Countdown (vol 1)"


def test_reader_init_access_and_age_restriction_guards(auth_client, db, normal_user):
    library, _, volume = _create_graph(
        db,
        lib_name="reader-guard-lib",
        series_name="Reader Guard",
    )
    comic = _add_comic(
        db,
        volume,
        number="1",
        title="Guarded Comic",
        age_rating="Mature 17+",
        page_count=12,
    )
    db.commit()

    no_access = auth_client.get(f"/api/reader/{comic.id}/read-init")
    assert no_access.status_code == 404
    assert no_access.json() == {"detail": "Comic not found"}

    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    restricted = auth_client.get(f"/api/reader/{comic.id}/read-init")
    assert restricted.status_code == 403
    assert restricted.json() == {"detail": "Content restricted by age rating"}


def test_reader_init_pull_list_context_and_value_error_fallback(auth_client, db, normal_user):
    library, _, volume = _create_graph(
        db,
        lib_name="reader-pull-lib",
        series_name="Reader Pull",
    )

    safe = _add_comic(db, volume, number="1", title="Pull Safe", age_rating="Teen", page_count=20)
    banned = _add_comic(db, volume, number="2", title="Pull Banned", age_rating="Mature 17+", page_count=20)

    list_with_safe = PullList(user_id=normal_user.id, name="Pull With Safe")
    list_without_safe = PullList(user_id=normal_user.id, name="Pull Without Safe")
    db.add_all([list_with_safe, list_without_safe])
    db.flush()

    db.add_all([
        PullListItem(pull_list_id=list_with_safe.id, comic_id=banned.id, sort_order=1),
        PullListItem(pull_list_id=list_with_safe.id, comic_id=safe.id, sort_order=2),
        PullListItem(pull_list_id=list_without_safe.id, comic_id=banned.id, sort_order=1),
    ])

    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    scoped = auth_client.get(
        f"/api/reader/{safe.id}/read-init?context_type=pull_list&context_id={list_with_safe.id}"
    )
    assert scoped.status_code == 200
    scoped_payload = scoped.json()
    assert scoped_payload["context_label"] == "Pull With Safe"
    assert scoped_payload["context_total"] == 1
    assert scoped_payload["context_position"] == 1
    assert scoped_payload["prev_comic_id"] is None
    assert scoped_payload["next_comic_id"] is None

    missing_membership = auth_client.get(
        f"/api/reader/{safe.id}/read-init?context_type=pull_list&context_id={list_without_safe.id}"
    )
    assert missing_membership.status_code == 200
    fallback_payload = missing_membership.json()
    assert fallback_payload["context_label"] == "Pull Without Safe"
    assert fallback_payload["context_total"] == 0
    assert fallback_payload["context_position"] == 0
    assert fallback_payload["prev_comic_id"] is None
    assert fallback_payload["next_comic_id"] is None


def test_reader_init_reading_list_collection_and_series_contexts(auth_client, db, normal_user):
    library, alpha_series, alpha_volume = _create_graph(
        db,
        lib_name="reader-context-lib",
        series_name="Alpha Line",
        volume_number=1,
    )

    alpha1 = _add_comic(db, alpha_volume, number="1", title="Alpha One", year=2001, page_count=12)
    alpha2 = _add_comic(db, alpha_volume, number="2", title="Alpha Two", year=2001, page_count=12)

    _, reverse_series, reverse_volume = _create_graph(
        db,
        lib_name="reader-context-lib-2",
        series_name="Countdown",
        volume_number=1,
    )
    # Keep reverse series in same accessible library by reassigning.
    reverse_series.library_id = library.id
    db.flush()

    rev1 = _add_comic(db, reverse_volume, number="1", title="Rev One", year=2010, page_count=12)
    rev2 = _add_comic(db, reverse_volume, number="2", title="Rev Two", year=2010, page_count=12)
    rev3 = _add_comic(db, reverse_volume, number="3", title="Rev Three", year=2010, page_count=12)

    reading_list = ReadingList(name="Reader List", description="")
    collection = Collection(name="Reader Collection", description="")
    db.add_all([reading_list, collection])
    db.flush()

    db.add_all([
        ReadingListItem(reading_list_id=reading_list.id, comic_id=alpha1.id, position=1),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=alpha2.id, position=2),
        CollectionItem(collection_id=collection.id, comic_id=rev2.id),
        CollectionItem(collection_id=collection.id, comic_id=alpha1.id),
    ])

    normal_user.accessible_libraries.append(library)
    db.commit()

    rl_resp = auth_client.get(
        f"/api/reader/{alpha2.id}/read-init?context_type=reading_list&context_id={reading_list.id}"
    )
    assert rl_resp.status_code == 200
    rl = rl_resp.json()
    assert rl["context_label"] == "Reader List"
    assert rl["prev_comic_id"] == alpha1.id
    assert rl["next_comic_id"] is None
    assert rl["context_total"] == 2
    assert rl["context_position"] == 2

    coll_resp = auth_client.get(
        f"/api/reader/{rev2.id}/read-init?context_type=collection&context_id={collection.id}"
    )
    assert coll_resp.status_code == 200
    coll = coll_resp.json()
    assert coll["context_label"] == "Reader Collection"
    assert coll["prev_comic_id"] == alpha1.id
    assert coll["next_comic_id"] is None

    series_resp = auth_client.get(
        f"/api/reader/{rev2.id}/read-init?context_type=series&context_id={reverse_series.id}"
    )
    assert series_resp.status_code == 200
    series_payload = series_resp.json()
    assert series_payload["context_label"] == "Countdown"
    assert series_payload["prev_comic_id"] == rev3.id
    assert series_payload["next_comic_id"] == rev1.id


def test_reader_page_endpoint_headers_and_errors(client, db):
    _, _, volume = _create_graph(db, lib_name="reader-page-lib", series_name="Reader Pages")
    comic = _add_comic(db, volume, number="1", title="Page Comic")
    db.commit()

    missing = client.get("/api/reader/999999/page/1")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Comic not found"}

    with patch("app.api.reader.ImageService.get_page_image", return_value=(b"jpeg-bytes", True, "image/jpeg")) as mock_page:
        jpeg = client.get(f"/api/reader/{comic.id}/page/1?sharpen=true&grayscale=true")

    assert jpeg.status_code == 200
    assert jpeg.headers["content-disposition"] == 'inline; filename="page_1.jpg"'
    assert jpeg.headers["cache-control"] == "public, max-age=31536000"
    mock_page.assert_called_once_with(
        str(comic.file_path),
        1,
        sharpen=True,
        grayscale=True,
        transcode_webp=False,
    )

    with patch("app.api.reader.ImageService.get_page_image", return_value=(b"png-bytes", False, "image/png")):
        png = client.get(f"/api/reader/{comic.id}/page/2")

    assert png.status_code == 200
    assert png.headers["content-disposition"] == 'inline; filename="page_2.png"'
    assert png.headers["cache-control"] == "no-cache, no-store, must-revalidate"

    with patch("app.api.reader.ImageService.get_page_image", return_value=(b"gif-bytes", False, "image/gif")):
        gif = client.get(f"/api/reader/{comic.id}/page/3")

    assert gif.status_code == 200
    assert gif.headers["content-disposition"] == 'inline; filename="page_3.gif"'

    with patch("app.api.reader.ImageService.get_page_image", return_value=(b"webp-bytes", True, "image/webp")):
        webp = client.get(f"/api/reader/{comic.id}/page/4?webp=true")

    assert webp.status_code == 200
    assert webp.headers["content-disposition"] == 'inline; filename="page_4.webp"'

    with patch("app.api.reader.ImageService.get_page_image", return_value=(None, False, "image/jpeg")):
        no_page = client.get(f"/api/reader/{comic.id}/page/5")

    assert no_page.status_code == 404
    assert no_page.json() == {"detail": "Page not found"}
