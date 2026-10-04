import logging
from datetime import datetime, timezone, timedelta
import pytest
import xml.etree.ElementTree as ET
from PIL import Image
from app.services.settings_service import SettingsService
from app.models.setting import SystemSetting
from app.models.collection import Collection, CollectionItem
from app.models.series import Series
from app.models.comic import Comic, Volume
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.reading_progress import ReadingProgress
from tests.factories import create_library_with_root


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def test_opds_disabled_by_default(client, normal_user):
    """
    Ensure that even with valid credentials, OPDS returns 503
    if the feature is disabled in settings.
    """
    # 1. Ensure setting is False (Default)
    # Note: We don't need to mock SettingsService, we just update the DB via the fixture
    # The fixture 'client' uses the same 'db' session.

    # 2. Try to access root feed with Basic Auth
    response = client.get(
        "/opds/",
        auth=(normal_user.username, "fakehash")  # "fakehash" matches the user fixture
    )

    # 3. Should fail with Service Unavailable (not 401, but 503)
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]


def test_opds_auth_flow(client, db, normal_user):
    """
    Test the full flow: Enable Setting -> Bad Auth -> Good Auth
    """
    # 1. Enable OPDS
    # We manually update the DB to simulate the Admin toggling it ON
    setting = db.query(SystemSetting).filter(SystemSetting.key == "server.opds_enabled").first()
    if not setting:
        # Create if missing (though initialize_defaults usually handles this)
        setting = SystemSetting(
            key="server.opds_enabled",
            value="true",
            category="server",
            data_type="bool"
        )
        db.add(setting)
    else:
        setting.value = "true"
    db.commit()

    # 2. Try with WRONG password
    response = client.get(
        "/opds/",
        auth=(normal_user.username, "wrong_password")
    )
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert response.headers["WWW-Authenticate"] == 'Basic realm="Parker OPDS"'

    # 3. Try with CORRECT password (using the fixture's password)
    # Note: The 'normal_user' fixture sets hashed_password="fakehash".
    # In a real app, verify_password checks hash.
    # IN TESTING: We need to make sure verify_password works with our fixture.
    # If your 'verify_password' implementation in 'app/core/security.py' uses bcrypt,
    # 'fakehash' won't work unless we mock verify_password or create a valid hash.

    # For this test to pass with the 'normal_user' fixture as written,
    # we assume we need to patch 'verify_password' to return True.
    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            "/opds/",
            auth=(normal_user.username, "any_password")
        )

        assert response.status_code == 200
        assert "application/atom+xml" in response.headers["content-type"]
        assert "<feed" in response.text
        assert "Parker Library" in response.text


def test_opds_logs_invalid_password(client, db, normal_user, caplog):
    _enable_opds(db)
    caplog.set_level(logging.WARNING, logger="app.auth")

    response = client.get(
        "/opds/",
        auth=(normal_user.username, "wrong_password")
    )

    assert response.status_code == 401
    assert any(
        "Authentication failed via OPDS basic auth" in record.message
        and "reason=invalid_password" in record.message
        and f"username='{normal_user.username}'" in record.message
        for record in caplog.records
    )


def test_opds_logs_unknown_user(client, db, caplog):
    _enable_opds(db)
    caplog.set_level(logging.WARNING, logger="app.auth")

    response = client.get(
        "/opds/",
        auth=("missing-user", "wrong_password")
    )

    assert response.status_code == 401
    assert any(
        "Authentication failed via OPDS basic auth" in record.message
        and "reason=unknown_user" in record.message
        and "username='missing-user'" in record.message
        for record in caplog.records
    )


def test_opds_missing_credentials_returns_basic_realm(client, db):
    _enable_opds(db)

    response = client.get("/opds/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="Parker OPDS"'


def _enable_opds(db):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "server.opds_enabled").first()
    if not setting:
        setting = SystemSetting(
            key="server.opds_enabled",
            value="true",
            category="server",
            data_type="bool"
        )
        db.add(setting)
    else:
        setting.value = "true"
    db.commit()


def _opds_entry_titles(response_text):
    root = ET.fromstring(response_text)
    return [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in root.findall("atom:entry", ATOM_NS)]


def _opds_navigation_entries(response_text):
    root = ET.fromstring(response_text)
    return [
        entry for entry in root.findall("atom:entry", ATOM_NS)
        if entry.find("atom:link[@rel='subsection']", ATOM_NS) is not None
    ]


def _opds_acquisition_entries(response_text):
    root = ET.fromstring(response_text)
    return [
        entry for entry in root.findall("atom:entry", ATOM_NS)
        if entry.find("atom:link[@rel='http://opds-spec.org/acquisition']", ATOM_NS) is not None
    ]


def test_opds_root_includes_shortcut_feeds_and_libraries(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Root OPDS Library", "/tmp/opds-root-library")
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get("/opds/", auth=(normal_user.username, "any_password"))

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    entries = root.findall("atom:entry", ATOM_NS)
    titles = [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in entries]
    assert titles[:5] == [
        "Continue Reading",
        "Recently Added",
        "Recently Updated",
        "Collections",
        "Reading Lists",
    ]
    assert "Root OPDS Library" in titles

    links = {
        entry.findtext("atom:title", namespaces=ATOM_NS):
            entry.find("atom:link[@rel='subsection']", ATOM_NS).get("href")
        for entry in entries
    }
    assert links["Continue Reading"] == "http://testserver/opds/continue-reading"
    assert links["Recently Added"] == "http://testserver/opds/recently-added"
    assert links["Recently Updated"] == "http://testserver/opds/recently-updated"
    assert links["Collections"] == "http://testserver/opds/collections"
    assert links["Reading Lists"] == "http://testserver/opds/reading-lists"
    assert links["Root OPDS Library"] == f"http://testserver/opds/libraries/{library.id}"


def test_opds_library_feed_renders_series_entries(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "OPDS Library", "/tmp/opds-library")
    root = library.active_root
    series = Series(name="Alpha Flight", library=library, summary_override="Team book")
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="First Issue",
        filename="alpha-flight-001.cbz",
        library_root_id=root.id,
        relative_path="alpha-flight-001.cbz",
        updated_at=series.updated_at,
    )
    db.add_all([series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/libraries/{library.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    assert len(entries) == 1
    assert entries[0].findtext("atom:title", namespaces=ns) == "Alpha Flight"
    subsection = entries[0].find("atom:link[@rel='subsection']", ns)
    assert subsection is not None
    assert subsection.get("href") == f"http://testserver/opds/series/{series.id}"
    assert "Team book" in response.text


def test_opds_library_feed_paginates_series_entries(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Paged Library", "/tmp/opds-paged-library")
    root = library.active_root
    series_names = ["Alpha", "Beta", "Gamma"]
    for name in series_names:
        series = Series(name=name, library=library)
        volume = Volume(series=series, volume_number=1)
        comic = Comic(
            volume=volume,
            number="1",
            title=f"{name} One",
            filename=f"{name.lower()}-001.cbz",
            library_root_id=root.id,
            relative_path=f"{name.lower()}-001.cbz",
        )
        db.add_all([series, volume, comic])

    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/libraries/{library.id}?page=1&size=2",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    assert [entry.findtext("atom:title", namespaces=ns) for entry in entries] == ["Alpha", "Beta"]

    next_link = root.find("atom:link[@rel='next']", ns)
    last_link = root.find("atom:link[@rel='last']", ns)
    assert next_link is not None
    assert next_link.get("href") == f"http://testserver/opds/libraries/{library.id}?page=2&size=2"
    assert last_link is not None
    assert last_link.get("href") == f"http://testserver/opds/libraries/{library.id}?page=2&size=2"


def test_opds_continue_reading_feed_returns_started_unfinished_issues(client, db, normal_user):
    _enable_opds(db)

    now = datetime.now(timezone.utc)
    library = create_library_with_root(db, "Resume OPDS Library", "/tmp/opds-resume-library")
    root = library.active_root
    series = Series(name="Resume Series", library=library)
    volume = Volume(series=series, volume_number=1)
    started = Comic(
        volume=volume,
        number="2",
        title="Resume Me",
        filename="resume-002.cbz",
        library_root_id=root.id,
        relative_path="resume-002.cbz",
        file_size=456,
    )
    unstarted = Comic(
        volume=volume,
        number="1",
        title="Not Started",
        filename="resume-001.cbz",
        library_root_id=root.id,
        relative_path="resume-001.cbz",
        file_size=123,
    )
    completed = Comic(
        volume=volume,
        number="3",
        title="Already Done",
        filename="resume-003.cbz",
        library_root_id=root.id,
        relative_path="resume-003.cbz",
        file_size=789,
    )
    db.add_all([
        series,
        volume,
        started,
        unstarted,
        completed,
        ReadingProgress(
            user=normal_user,
            comic=started,
            current_page=4,
            total_pages=20,
            completed=False,
            last_read_at=now,
        ),
        ReadingProgress(
            user=normal_user,
            comic=unstarted,
            current_page=0,
            total_pages=20,
            completed=False,
            last_read_at=now - timedelta(minutes=1),
        ),
        ReadingProgress(
            user=normal_user,
            comic=completed,
            current_page=20,
            total_pages=20,
            completed=True,
            last_read_at=now - timedelta(minutes=2),
        ),
    ])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get("/opds/continue-reading", auth=(normal_user.username, "any_password"))

    assert response.status_code == 200
    entries = _opds_acquisition_entries(response.text)
    assert [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in entries] == [
        "Resume Series #2 - Resume Me"
    ]
    acquisition = entries[0].find(
        "atom:link[@rel='http://opds-spec.org/acquisition']",
        ATOM_NS,
    )
    assert acquisition is not None
    assert acquisition.get("href", "").startswith(f"http://testserver/opds/download/{started.id}/")


def test_opds_recent_feeds_list_series_by_comic_activity(client, db, normal_user):
    _enable_opds(db)

    now = datetime.now(timezone.utc)
    library = create_library_with_root(db, "Recent OPDS Library", "/tmp/opds-recent-library")
    root = library.active_root

    older_added_series = Series(name="Old Import New Update", library=library)
    older_added_volume = Volume(series=older_added_series, volume_number=1)
    older_added_comic = Comic(
        volume=older_added_volume,
        number="1",
        title="Older Import",
        filename="old-import-new-update-001.cbz",
        library_root_id=root.id,
        relative_path="old-import-new-update-001.cbz",
        created_at=now - timedelta(days=2),
        updated_at=now,
    )

    newer_added_series = Series(name="New Import Old Update", library=library)
    newer_added_volume = Volume(series=newer_added_series, volume_number=1)
    newer_added_comic = Comic(
        volume=newer_added_volume,
        number="1",
        title="Newer Import",
        filename="new-import-old-update-001.cbz",
        library_root_id=root.id,
        relative_path="new-import-old-update-001.cbz",
        created_at=now,
        updated_at=now - timedelta(days=2),
    )

    db.add_all([
        older_added_series,
        older_added_volume,
        older_added_comic,
        newer_added_series,
        newer_added_volume,
        newer_added_comic,
    ])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        added_response = client.get("/opds/recently-added", auth=(normal_user.username, "any_password"))
        updated_response = client.get("/opds/recently-updated", auth=(normal_user.username, "any_password"))

    assert added_response.status_code == 200
    assert updated_response.status_code == 200
    assert _opds_entry_titles(added_response.text) == ["New Import Old Update", "Old Import New Update"]
    assert _opds_entry_titles(updated_response.text) == ["Old Import New Update", "New Import Old Update"]


def test_opds_collections_feed_and_detail_use_visible_collection_items(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Collection OPDS Library", "/tmp/opds-collection-library")
    root = library.active_root
    series = Series(name="Collection Series", library=library)
    volume = Volume(series=series, volume_number=1)
    newer = Comic(
        volume=volume,
        number="2",
        title="Second Chronology",
        filename="collection-002.cbz",
        library_root_id=root.id,
        relative_path="collection-002.cbz",
        year=2022,
        file_size=222,
    )
    older = Comic(
        volume=volume,
        number="1",
        title="First Chronology",
        filename="collection-001.cbz",
        library_root_id=root.id,
        relative_path="collection-001.cbz",
        year=2020,
        file_size=111,
    )
    db.add_all([series, volume, newer, older])

    hidden_library = create_library_with_root(db, "Hidden Collection OPDS Library", "/tmp/opds-hidden-collection-library")
    hidden_root = hidden_library.active_root
    hidden_series = Series(name="Hidden Collection Series", library=hidden_library)
    hidden_volume = Volume(series=hidden_series, volume_number=1)
    hidden_comic = Comic(
        volume=hidden_volume,
        number="1",
        title="Hidden Collection Issue",
        filename="hidden-collection-001.cbz",
        library_root_id=hidden_root.id,
        relative_path="hidden-collection-001.cbz",
    )
    db.add_all([hidden_series, hidden_volume, hidden_comic])

    parse_off_library = create_library_with_root(db, "Parse Off Collection OPDS Library", "/tmp/opds-parse-off-collection-library")
    parse_off_library.parse_collections = False
    parse_off_root = parse_off_library.active_root
    parse_off_series = Series(name="Parse Off Collection Series", library=parse_off_library)
    parse_off_volume = Volume(series=parse_off_series, volume_number=1)
    parse_off_comic = Comic(
        volume=parse_off_volume,
        number="1",
        title="Parse Off Collection Issue",
        filename="parse-off-collection-001.cbz",
        library_root_id=parse_off_root.id,
        relative_path="parse-off-collection-001.cbz",
    )

    visible_collection = Collection(name="Visible OPDS Collection", description="Visible collection", auto_generated=0)
    hidden_collection = Collection(name="Hidden OPDS Collection", auto_generated=0)
    parse_off_collection = Collection(name="Parse Off OPDS Collection", auto_generated=0)
    db.add_all([
        parse_off_series,
        parse_off_volume,
        parse_off_comic,
        visible_collection,
        hidden_collection,
        parse_off_collection,
    ])
    db.flush()
    db.add_all([
        CollectionItem(collection_id=visible_collection.id, comic_id=newer.id),
        CollectionItem(collection_id=visible_collection.id, comic_id=older.id),
        CollectionItem(collection_id=hidden_collection.id, comic_id=hidden_comic.id),
        CollectionItem(collection_id=parse_off_collection.id, comic_id=parse_off_comic.id),
    ])
    normal_user.accessible_libraries.extend([library, parse_off_library])
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        list_response = client.get("/opds/collections", auth=(normal_user.username, "any_password"))
        detail_response = client.get(
            f"/opds/collections/{visible_collection.id}",
            auth=(normal_user.username, "any_password"),
        )

    assert list_response.status_code == 200
    nav_entries = _opds_navigation_entries(list_response.text)
    assert [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in nav_entries] == [
        "Visible OPDS Collection"
    ]
    subsection = nav_entries[0].find("atom:link[@rel='subsection']", ATOM_NS)
    assert subsection is not None
    assert subsection.get("href") == f"http://testserver/opds/collections/{visible_collection.id}"
    assert "Visible collection" in list_response.text
    assert "2 issues." in list_response.text

    assert detail_response.status_code == 200
    assert _opds_entry_titles(detail_response.text) == [
        "Collection Series #1 - First Chronology",
        "Collection Series #2 - Second Chronology",
    ]


def test_opds_reading_lists_feed_and_detail_preserve_reading_order(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Reading List OPDS Library", "/tmp/opds-reading-list-library")
    root = library.active_root
    series = Series(name="Reading List Series", library=library)
    volume = Volume(series=series, volume_number=1)
    first = Comic(
        volume=volume,
        number="7",
        title="Event Opens",
        filename="reading-list-007.cbz",
        library_root_id=root.id,
        relative_path="reading-list-007.cbz",
        file_size=777,
    )
    second = Comic(
        volume=volume,
        number="1",
        title="Event Continues",
        filename="reading-list-001.cbz",
        library_root_id=root.id,
        relative_path="reading-list-001.cbz",
        file_size=111,
    )
    db.add_all([series, volume, first, second])

    parse_off_library = create_library_with_root(db, "Parse Off Reading List OPDS Library", "/tmp/opds-parse-off-reading-list-library")
    parse_off_library.parse_reading_lists = False
    parse_off_root = parse_off_library.active_root
    parse_off_series = Series(name="Parse Off Reading List Series", library=parse_off_library)
    parse_off_volume = Volume(series=parse_off_series, volume_number=1)
    parse_off_comic = Comic(
        volume=parse_off_volume,
        number="1",
        title="Parse Off Reading List Issue",
        filename="parse-off-reading-list-001.cbz",
        library_root_id=parse_off_root.id,
        relative_path="parse-off-reading-list-001.cbz",
    )

    visible_list = ReadingList(name="Visible OPDS Reading List", description="Visible reading list", source="manual")
    parse_off_list = ReadingList(name="Parse Off OPDS Reading List", source="manual")
    db.add_all([
        parse_off_series,
        parse_off_volume,
        parse_off_comic,
        visible_list,
        parse_off_list,
    ])
    db.flush()
    db.add_all([
        ReadingListItem(reading_list_id=visible_list.id, comic_id=second.id, position=2),
        ReadingListItem(reading_list_id=visible_list.id, comic_id=first.id, position=1),
        ReadingListItem(reading_list_id=parse_off_list.id, comic_id=parse_off_comic.id, position=1),
    ])
    normal_user.accessible_libraries.extend([library, parse_off_library])
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        list_response = client.get("/opds/reading-lists", auth=(normal_user.username, "any_password"))
        detail_response = client.get(
            f"/opds/reading-lists/{visible_list.id}",
            auth=(normal_user.username, "any_password"),
        )

    assert list_response.status_code == 200
    nav_entries = _opds_navigation_entries(list_response.text)
    assert [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in nav_entries] == [
        "Visible OPDS Reading List"
    ]
    subsection = nav_entries[0].find("atom:link[@rel='subsection']", ATOM_NS)
    assert subsection is not None
    assert subsection.get("href") == f"http://testserver/opds/reading-lists/{visible_list.id}"
    assert "Visible reading list" in list_response.text
    assert "2 issues." in list_response.text

    assert detail_response.status_code == 200
    assert _opds_entry_titles(detail_response.text) == [
        "Reading List Series #7 - Event Opens",
        "Reading List Series #1 - Event Continues",
    ]


def test_opds_container_feeds_reject_age_restricted_content(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Restricted Container OPDS Library", "/tmp/opds-restricted-container-library")
    root = library.active_root
    series = Series(name="Restricted Container Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Restricted Issue",
        filename="restricted-container-001.cbz",
        library_root_id=root.id,
        relative_path="restricted-container-001.cbz",
        age_rating="Mature 17+",
    )
    collection = Collection(name="Restricted OPDS Collection", auto_generated=0)
    reading_list = ReadingList(name="Restricted OPDS Reading List", source="manual")
    db.add_all([series, volume, comic, collection, reading_list])
    db.flush()
    db.add_all([
        CollectionItem(collection_id=collection.id, comic_id=comic.id),
        ReadingListItem(reading_list_id=reading_list.id, comic_id=comic.id, position=1),
    ])
    normal_user.accessible_libraries.append(library)
    normal_user.max_age_rating = "Teen"
    normal_user.allow_unknown_age_ratings = False
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        collections_response = client.get("/opds/collections", auth=(normal_user.username, "any_password"))
        reading_lists_response = client.get("/opds/reading-lists", auth=(normal_user.username, "any_password"))
        collection_response = client.get(
            f"/opds/collections/{collection.id}",
            auth=(normal_user.username, "any_password"),
        )
        reading_list_response = client.get(
            f"/opds/reading-lists/{reading_list.id}",
            auth=(normal_user.username, "any_password"),
        )

    assert collections_response.status_code == 200
    assert _opds_navigation_entries(collections_response.text) == []
    assert reading_lists_response.status_code == 200
    assert _opds_navigation_entries(reading_lists_response.text) == []
    assert collection_response.status_code == 403
    assert "age-restricted" in collection_response.json()["detail"].lower()
    assert reading_list_response.status_code == 403
    assert "age-restricted" in reading_list_response.json()["detail"].lower()


def test_opds_series_feed_handles_missing_month_and_day(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Series Library", "/tmp/opds-series-library")
    root = library.active_root
    series = Series(name="Beta Ray", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="7",
        title="Stormbreaker",
        filename="beta-ray-007.cbz",
        library_root_id=root.id,
        relative_path="beta-ray-007.cbz",
        year=2024,
        month=None,
        day=None,
        file_size=12345,
    )
    db.add_all([series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/series/{series.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom", "dcterms": "http://purl.org/dc/terms/"}
    entry = root.find("atom:entry", ns)
    assert entry is not None
    assert entry.findtext("dcterms:issued", namespaces=ns) == "2024-01-01"
    acquisition = entry.find("atom:link[@rel='http://opds-spec.org/acquisition']", ns)
    assert acquisition is not None
    acquisitions = entry.findall("atom:link[@rel='http://opds-spec.org/acquisition']", ns)
    assert len(acquisitions) == 1
    assert acquisition.get("type") == "application/vnd.comicbook+zip"
    assert acquisition.get("href", "").startswith("http://testserver/opds/download/")
    assert acquisition.get("href", "").endswith("/Comic%20-%20Stormbreaker.cbz")


def test_opds_series_feed_hides_inaccessible_library_series(client, db, normal_user):
    _enable_opds(db)

    hidden_library = create_library_with_root(db, "Hidden OPDS Library", "/tmp/hidden-opds-library")
    root = hidden_library.active_root
    series = Series(name="Hidden OPDS Series", library=hidden_library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Hidden OPDS Issue",
        filename="hidden-opds-001.cbz",
        library_root_id=root.id,
        relative_path="hidden-opds-001.cbz",
    )
    db.add_all([series, volume, comic])
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/series/{series.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Series not found"}


def test_opds_series_feed_paginates_issue_entries(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Paged Series Library", "/tmp/opds-paged-series-library")
    root = library.active_root
    series = Series(name="Paged Issues", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    for number in ("1", "2", "3"):
        comic = Comic(
            volume=volume,
            number=number,
            title=f"Issue {number}",
            filename=f"paged-issues-{number}.cbz",
            library_root_id=root.id,
            relative_path=f"paged-issues-{number}.cbz",
            file_size=123,
        )
        db.add(comic)

    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/series/{series.id}?page=2&size=2",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    assert len(entries) == 1
    assert entries[0].findtext("atom:title", namespaces=ns) == "Paged Issues #3 - Issue 3"

    previous_link = root.find("atom:link[@rel='previous']", ns)
    next_link = root.find("atom:link[@rel='next']", ns)
    assert previous_link is not None
    assert previous_link.get("href") == f"http://testserver/opds/series/{series.id}?page=1&size=2"
    assert next_link is None


def test_opds_series_feed_links_to_volume_browser_for_multi_volume_series(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Multi Volume OPDS Library", "/tmp/opds-multi-volume-library")
    root = library.active_root
    series = Series(name="Multi Volume Series", library=library)
    volume_one = Volume(series=series, volume_number=1)
    volume_two = Volume(series=series, volume_number=2)
    comic_one = Comic(
        volume=volume_one,
        number="1",
        title="First Volume",
        filename="multi-volume-001.cbz",
        library_root_id=root.id,
        relative_path="multi-volume-001.cbz",
    )
    comic_two = Comic(
        volume=volume_two,
        number="1",
        title="Second Volume",
        filename="multi-volume-v2-001.cbz",
        library_root_id=root.id,
        relative_path="multi-volume-v2-001.cbz",
    )
    db.add_all([series, volume_one, volume_two, comic_one, comic_two])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(f"/opds/series/{series.id}", auth=(normal_user.username, "any_password"))

    assert response.status_code == 200
    nav_entries = _opds_navigation_entries(response.text)
    assert [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in nav_entries] == [
        "Browse Volumes"
    ]
    subsection = nav_entries[0].find(
        "atom:link[@rel='subsection']",
        ATOM_NS,
    )
    assert subsection is not None
    assert subsection.get("href") == f"http://testserver/opds/series/{series.id}/volumes"

    acquisition_titles = [
        entry.findtext("atom:title", namespaces=ATOM_NS)
        for entry in _opds_acquisition_entries(response.text)
    ]
    assert acquisition_titles == [
        "Multi Volume Series #1 - First Volume",
        "Multi Volume Series #1 - Second Volume",
    ]


def test_opds_series_volumes_and_volume_feed(client, db, normal_user):
    _enable_opds(db)

    library = create_library_with_root(db, "Structured OPDS Library", "/tmp/opds-structured-library")
    root = library.active_root
    series = Series(name="Structured Series", library=library)
    volume_one = Volume(series=series, volume_number=1, summary_override="Original run")
    volume_two = Volume(series=series, volume_number=2, summary_override="Relaunch")
    comic_one = Comic(
        volume=volume_one,
        number="1",
        title="Original One",
        filename="structured-v1-001.cbz",
        library_root_id=root.id,
        relative_path="structured-v1-001.cbz",
    )
    comic_two = Comic(
        volume=volume_two,
        number="1",
        title="Relaunch One",
        filename="structured-v2-001.cbz",
        library_root_id=root.id,
        relative_path="structured-v2-001.cbz",
    )
    db.add_all([series, volume_one, volume_two, comic_one, comic_two])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        volumes_response = client.get(
            f"/opds/series/{series.id}/volumes",
            auth=(normal_user.username, "any_password"),
        )
        volume_response = client.get(
            f"/opds/volumes/{volume_two.id}",
            auth=(normal_user.username, "any_password"),
        )

    assert volumes_response.status_code == 200
    nav_entries = _opds_navigation_entries(volumes_response.text)
    assert [entry.findtext("atom:title", namespaces=ATOM_NS) for entry in nav_entries] == ["Volume 1", "Volume 2"]
    assert [
        entry.find("atom:link[@rel='subsection']", ATOM_NS).get("href")
        for entry in nav_entries
    ] == [
        f"http://testserver/opds/volumes/{volume_one.id}",
        f"http://testserver/opds/volumes/{volume_two.id}",
    ]
    assert "Original run" in volumes_response.text
    assert "Relaunch" in volumes_response.text

    assert volume_response.status_code == 200
    assert _opds_entry_titles(volume_response.text) == ["Structured Series #1 - Relaunch One"]


def test_opds_volume_feed_hides_inaccessible_library_volume(client, db, normal_user):
    _enable_opds(db)

    hidden_library = create_library_with_root(db, "Hidden Volume OPDS Library", "/tmp/opds-hidden-volume-library")
    root = hidden_library.active_root
    series = Series(name="Hidden Volume Series", library=hidden_library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Hidden Volume Issue",
        filename="hidden-volume-001.cbz",
        library_root_id=root.id,
        relative_path="hidden-volume-001.cbz",
    )
    db.add_all([series, volume, comic])
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(f"/opds/volumes/{volume.id}", auth=(normal_user.username, "any_password"))

    assert response.status_code == 404
    assert response.json() == {"detail": "Volume not found"}


def test_opds_thumbnail_links_use_jpeg_endpoint(client, db, normal_user, tmp_path):
    _enable_opds(db)

    thumbnail_path = tmp_path / "cover.webp"
    Image.new("RGB", (24, 36), color=(120, 20, 40)).save(thumbnail_path, format="WEBP")

    library = create_library_with_root(db, "Thumbnail Library", str(tmp_path))
    root = library.active_root
    series = Series(name="Cover Test", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Cover Story",
        filename="cover-test-001.cbz",
        library_root_id=root.id,
        relative_path="cover-test-001.cbz",
        thumbnail_path=str(thumbnail_path),
    )
    db.add_all([series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/series/{series.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    image = root.find("atom:entry/atom:link[@rel='http://opds-spec.org/image']", ns)
    assert image is not None
    assert image.get("type") == "image/jpeg"
    assert image.get("href", "").startswith(f"http://testserver/opds/images/{comic.id}/thumbnail.jpg?v=")

    missing_auth = client.get(f"/opds/images/{comic.id}/thumbnail.jpg")
    assert missing_auth.status_code == 401

    with patch("app.api.opds_deps.verify_password", return_value=True):
        thumbnail_response = client.get(
            f"/opds/images/{comic.id}/thumbnail.jpg",
            auth=(normal_user.username, "any_password")
        )

        assert thumbnail_response.status_code == 200
        assert thumbnail_response.headers["content-type"].startswith("image/jpeg")
        assert thumbnail_response.content.startswith(b"\xff\xd8")


def test_opds_thumbnail_hides_inaccessible_library_comics(client, db, normal_user, tmp_path):
    _enable_opds(db)

    thumbnail_path = tmp_path / "hidden-cover.webp"
    Image.new("RGB", (24, 36), color=(80, 100, 160)).save(thumbnail_path, format="WEBP")

    library = create_library_with_root(db, "Hidden Thumbnail Library", str(tmp_path))
    root = library.active_root
    series = Series(name="Hidden Cover Test", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="Hidden Cover Story",
        filename="hidden-cover-test-001.cbz",
        library_root_id=root.id,
        relative_path="hidden-cover-test-001.cbz",
        thumbnail_path=str(thumbnail_path),
    )
    db.add_all([series, volume, comic])
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/images/{comic.id}/thumbnail.jpg",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 404


def test_opds_download_uses_real_archive_type_and_extension(client, db, normal_user, tmp_path):
    _enable_opds(db)

    archive_path = tmp_path / "gamma-ray-001.cbr"
    archive_path.write_bytes(b"fake-rar")

    library = create_library_with_root(db, "Download Library", str(tmp_path))
    root = library.active_root
    series = Series(name="Gamma Ray", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="1",
        title="First Blast",
        filename="gamma-ray-001.cbr",
        library_root_id=root.id,
        relative_path=archive_path.name,
    )
    db.add_all([series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/download/{comic.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.comicbook-rar")
    assert 'filename="Comic - First Blast.cbr"' in response.headers["content-disposition"]


def test_opds_download_uses_filename_stem_when_title_missing(client, db, normal_user, tmp_path):
    _enable_opds(db)

    archive_path = tmp_path / "titleless-special.cbz"
    archive_path.write_bytes(b"fake-zip")

    library = create_library_with_root(db, "Titleless Library", str(tmp_path))
    root = library.active_root
    series = Series(name="Titleless Series", library=library)
    volume = Volume(series=series, volume_number=1)
    comic = Comic(
        volume=volume,
        number="2",
        title=None,
        filename="titleless-special.cbz",
        library_root_id=root.id,
        relative_path=archive_path.name,
    )
    db.add_all([series, volume, comic])
    normal_user.accessible_libraries.append(library)
    db.commit()

    from unittest.mock import patch

    with patch("app.api.opds_deps.verify_password", return_value=True):
        response = client.get(
            f"/opds/download/{comic.id}",
            auth=(normal_user.username, "any_password")
        )

    assert response.status_code == 200
    assert 'filename="Comic - titleless-special.cbz"' in response.headers["content-disposition"]
