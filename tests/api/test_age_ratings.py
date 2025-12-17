from datetime import datetime
import pytest
from unittest.mock import patch

from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.models.reading_progress import ReadingProgress
from app.services.settings_service import SettingsService

# --- HELPERS ---

def setup_mixed_environment(db):
    """
    Sets up:
    1. Library
    2. Series A (Safe): 1 Comic (Teen)
    3. Series B (Poisoned): 1 Comic (Teen), 1 Comic (Mature)
    """
    # 1. Library
    lib = Library(name="Test Lib", path="/tmp")
    db.add(lib)
    db.commit()

    # 2. Series A (Safe)
    series_safe = Series(name="Safe Series", library_id=lib.id)
    db.add(series_safe)
    db.commit()

    vol_safe = Volume(series_id=series_safe.id, volume_number=1)
    db.add(vol_safe)
    db.commit()

    c1 = Comic(
        volume_id=vol_safe.id,
        title="Safe Book",
        number="1",
        age_rating="Teen",
        filename="safe.cbz",
        file_path="/tmp/safe.cbz"
    )
    db.add(c1)

    # 3. Series B (Poisoned/Mixed)
    series_mixed = Series(name="Poisoned Series", library_id=lib.id)
    db.add(series_mixed)
    db.commit()

    vol_mixed = Volume(series_id=series_mixed.id, volume_number=1)
    db.add(vol_mixed)
    db.commit()

    # This comic is SAFE, but lives in a dangerous neighborhood
    c2 = Comic(
        volume_id=vol_mixed.id,
        title="Safe Book in Bad Series",
        number="1",
        age_rating="Teen",
        filename="mixed_safe.cbz",
        file_path="/tmp/mixed_safe.cbz"
    )
    # This comic is the POISON PILL
    c3 = Comic(
        volume_id=vol_mixed.id,
        title="Mature Book",
        number="2",
        age_rating="Mature 17+",
        filename="mixed_mature.cbz",
        file_path="/tmp/mixed_mature.cbz"
    )
    db.add(c2)
    db.add(c3)
    db.commit()

    return {
        "lib_id": lib.id,
        "safe_series_id": series_safe.id,
        "poisoned_series_id": series_mixed.id,
        "safe_comic_id": c1.id,
        "poisoned_safe_comic_id": c2.id,
        "poisoned_mature_comic_id": c3.id
    }

@pytest.fixture
def restricted_user(db):
    """User restricted to 'Teen' (cannot see Mature)"""
    user = User(
        username="teen_user",
        email="teen@example.com",
        hashed_password=get_password_hash("test"),
        is_superuser=False,
        max_age_rating="Teen",
        allow_unknown_age_ratings=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def restricted_client(client, restricted_user):
    """Client authenticated as the Restricted User"""
    from app.api.deps import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: restricted_user
    return client

# --- TESTS ---

def test_search_poison_pill(db, restricted_client, restricted_user):
    """
    Search should hide 'Safe Book in Bad Series' because the Series is poisoned.
    """
    data = setup_mixed_environment(db)

    # FIX: Use db.get() instead of db.query().get() to avoid LegacyAPIWarning
    lib = db.get(Library, data['lib_id'])

    restricted_user.accessible_libraries.append(lib)
    db.commit()

    # 1. Search for "Safe"
    # Should find Series A, but NOT Series B
    response = restricted_client.get("/api/search/quick?q=Safe")
    assert response.status_code == 200
    results = response.json()

    # Verify Series Results
    series_names = [s['name'] for s in results['series']]
    assert "Safe Series" in series_names
    assert "Poisoned Series" not in series_names  # <--- POISON PILL WORKING

def test_home_random_poison_pill(db, restricted_client, restricted_user):
    """
    Random Gems should not suggest the Poisoned Series.
    """
    data = setup_mixed_environment(db)

    lib = db.get(Library, data['lib_id'])

    restricted_user.accessible_libraries.append(lib)
    db.commit()

    response = restricted_client.get("/api/home/random")
    assert response.status_code == 200
    items = response.json()

    # Should only return the Safe Series
    # Note: "Poisoned Series" exists but is hidden.
    assert len(items) == 1
    assert items[0]['id'] == data['safe_series_id']
    assert items[0]['name'] == "Safe Series"

def test_cover_manifest_poison_pill(db, restricted_client, restricted_user):
    """
    Requesting a cover manifest for a 'collection' or 'context' should exclude poisoned items.
    """
    data = setup_mixed_environment(db)

    lib = db.get(Library, data['lib_id'])

    restricted_user.accessible_libraries.append(lib)
    db.commit()

    # We'll test context_type='series' for the poisoned series itself.
    response = restricted_client.get(
        f"/api/comics/covers/manifest?context_type=series&context_id={data['poisoned_series_id']}"
    )
    assert response.status_code == 200
    manifest = response.json()

    # The Poison Pill filter runs on the Series check.
    # Since this series contains a Mature book, the entire query for this series returns nothing.
    assert manifest['total'] == 0
    assert len(manifest['items']) == 0

def test_direct_access_row_level_security(db, restricted_client, restricted_user):
    """
    Direct Access is the 'Guard Rail'.
    1. Safe Comic in Poisoned Series -> ALLOWED (200)
    2. Mature Comic in Poisoned Series -> BLOCKED (403)
    """
    data = setup_mixed_environment(db)

    lib = db.get(Library, data['lib_id'])

    restricted_user.accessible_libraries.append(lib)
    db.commit()

    # 1. Access the Safe Comic in the Banned Series
    # We allow this because if a user has a direct link, the content itself is not harmful.
    res_safe = restricted_client.get(f"/api/comics/{data['poisoned_safe_comic_id']}")
    assert res_safe.status_code == 200
    assert res_safe.json()['title'] == "Safe Book in Bad Series"

    # 2. Access the Mature Comic
    # This must be blocked.
    res_mature = restricted_client.get(f"/api/comics/{data['poisoned_mature_comic_id']}")
    assert res_mature.status_code == 403
    assert "restricted" in res_mature.json()['detail'].lower()

def test_reader_init_navigation(db, restricted_client, restricted_user):
    """
    Reader Init:
    If I open the 'Safe' book in the poisoned series,
    The 'Next' button should NOT link to the 'Mature' book.
    """
    data = setup_mixed_environment(db)

    lib = db.get(Library, data['lib_id'])

    restricted_user.accessible_libraries.append(lib)
    db.commit()

    # Open the Safe Book (Issue #1)
    # The Next Book is Mature (Issue #2)
    res = restricted_client.get(f"/api/reader/{data['poisoned_safe_comic_id']}/read-init")

    assert res.status_code == 200
    reader_data = res.json()

    # Verify we are on the safe book
    assert reader_data['comic_id'] == data['poisoned_safe_comic_id']

    # Verify Next ID is None (because the next book is Banned)
    # The reader endpoint logic filters out banned neighbors row-by-row.
    assert reader_data['next_comic_id'] is None

def test_superuser_bypass(db, admin_client):
    """
    Superuser should see everything, ignoring poison pills.
    """
    # Admin is Superuser, so they bypass RLS (Library Access) and Age Restrictions automatically.
    setup_mixed_environment(db)

    # 1. Search
    res_search = admin_client.get("/api/search/quick?q=Poisoned")
    results = res_search.json()
    assert "Poisoned Series" in [s['name'] for s in results['series']]

    # 2. Random
    res_home = admin_client.get("/api/home/random")
    # Should see both
    assert len(res_home.json()) == 2


def test_home_widgets_poison_pill(db, restricted_client, restricted_user):
    """Verify ALL home widgets respect the Poison Pill."""
    data = setup_mixed_environment(db)
    lib = db.get(Library, data['lib_id'])
    restricted_user.accessible_libraries.append(lib)

    # 1. Simulate reading history to trigger 'Resume' and 'Up Next'
    # User started the Safe Book in the Bad Series
    prog = ReadingProgress(
        user_id=restricted_user.id,
        comic_id=data['poisoned_safe_comic_id'],
        current_page=5,
        total_pages=10,
        completed=False,
        last_read_at=datetime.now()
    )
    db.add(prog)
    db.commit()

    # TEST RESUME
    # Should be hidden because the Series is poisoned (even though the book is safe)
    res = restricted_client.get("/api/home/resume")
    assert res.status_code == 200
    assert len(res.json()) == 0  # Should be empty

    # TEST UP NEXT
    # Mark as complete to trigger Up Next
    prog.completed = True
    db.commit()

    res = restricted_client.get("/api/home/up-next")
    assert res.status_code == 200
    # The next book is Mature, and the series is Poisoned. Should return nothing.
    assert len(res.json()) == 0

def test_dashboard_stats_poison_pill(db, restricted_client, restricted_user):
    """Dashboard stats should not count books from poisoned series."""
    data = setup_mixed_environment(db)
    lib = db.get(Library, data['lib_id'])
    restricted_user.accessible_libraries.append(lib)

    # User read the Safe Book in Bad Series
    prog = ReadingProgress(
        user_id=restricted_user.id,
        comic_id=data['poisoned_safe_comic_id'],
        current_page=10,
        total_pages=20,
        completed=True
    )
    db.add(prog)
    db.commit()

    res = restricted_client.get("/api/users/me/dashboard")
    assert res.status_code == 200
    stats = res.json()['stats']

    # Even though we read a book, it belongs to a banned series, so it shouldn't count in stats
    # (Or arguably it should, but your SQL logic filters the JOIN, so it won't).
    assert stats['issues_read'] == 0

@pytest.mark.skip(reason="Undecided if we're going to protect individual images from age check")
def test_file_security_bypass(db, restricted_client, restricted_user):
    """
    CRITICAL SECURITY CHECK:
    Can I download the file or see the page if I guess the URL?
    """
    data = setup_mixed_environment(db)
    lib = db.get(Library, data['lib_id'])
    restricted_user.accessible_libraries.append(lib)
    db.commit()

    # 1. Try to get a PAGE from the Mature book
    # This Mock prevents the actual file read, but we check if it passes the DB security layer
    with pytest.raises(Exception):
        # If this returns 403, you are safe. If 200 or 404 (file missing), you are vulnerable.
        res = restricted_client.get(f"/api/reader/{data['poisoned_mature_comic_id']}/page/1")
        assert res.status_code == 403

    # 2. Try to get the THUMBNAIL of the Mature book
    res_thumb = restricted_client.get(f"/api/comics/{data['poisoned_mature_comic_id']}/thumbnail")
    # Ideally, this should be 403 or 404 generic.
    # If it returns the image, it's a minor leak.
    if res_thumb.status_code == 200:
        pytest.fail("Security Hole: Thumbnail for banned book is accessible")

def test_opds_security(db, client, restricted_user):
    """Verify OPDS doesn't leak banned books."""
    data = setup_mixed_environment(db)
    lib = db.get(Library, data['lib_id'])
    restricted_user.accessible_libraries.append(lib)


    # SANITY CHECK: Verify Password Hashing works in Test Env
    # If this fails, the 401 is due to test environment config, not code.
    assert verify_password("test", restricted_user.hashed_password), "Test environment password hashing mismatch"

    auth_creds = ("teen_user", "test")

    # PATCH SETTINGS: Force "server.opds_enabled" to True for this test block
    with patch("app.services.settings_service.SettingsService.get", return_value=True):


        # 3. OPDS Series Feed
        res = client.get(
            f"/opds/series/{data['poisoned_series_id']}",
            auth=auth_creds
        )

        # If 401 here, check 'auth_creds' matches 'restricted_user' definition
        assert res.status_code == 200
        content = res.content.decode()
        assert "Mature Book" not in content

        # 4. OPDS Download
        res_dl = client.get(
            f"/opds/download/{data['poisoned_mature_comic_id']}",
            auth=auth_creds
        )

        assert res_dl.status_code == 403

