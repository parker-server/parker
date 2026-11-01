import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.templates import templates
from app.models.comic import Comic, Volume
from app.models.interactions import UserVolumeFollow
from app.models.notification import Notification
from app.models.reading_progress import ReadingProgress
from app.models.series import Series
from app.models.setting import SystemSetting
from app.models.user import User
from app.services.notifications import producers
from app.services.notifications.kinds import KINDS, NotificationKind
from app.services.notifications.producers import (
    WHATS_NEW_FILE,
    ScanInfo,
    WhatsNewEntry,
    announce_whats_new,
    announce_year_in_review,
    load_whats_new_entries,
    notify_follow_arrivals,
    run_notification_checks,
    run_scan_producers,
    run_startup_producers,
    whats_new_for_user,
    year_in_review_year,
)
from app.services.notifications.service import NotificationService
from app.services.settings_service import SettingsService
from tests.factories import create_comic, create_library_with_root


# Sample data for every registered kind. A new kind must add an entry here, which
# makes sure its render and link functions are exercised.
SAMPLE_DATA = {
    "follow_arrivals": {"volume_id": 12, "series_name": "Saga", "volume_number": 1},
    "year_in_review": {"year": 2026},
    "whats_new": {"version": "0.1.48", "first_title": "Notifications"},
}


def _user(db, username: str, *, admin: bool = False, active: bool = True, preferences=None) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="x",
        is_superuser=admin,
        is_active=active,
        notification_preferences=preferences,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_setting(db, key: str, value: str, data_type: str = "bool") -> None:
    setting = db.get(SystemSetting, key)
    if setting is None:
        setting = SystemSetting(key=key, category="test", data_type=data_type)
        db.add(setting)
    setting.value = value
    db.commit()


def _notify(db, user_ids, count=1, group_key=None, data=None):
    return NotificationService(db).notify(
        "whats_new",
        user_ids,
        data=data or {"version": "0.1.48", "first_title": "First"},
        group_key=group_key,
        count=count,
    )


@pytest.fixture
def test_kind(monkeypatch):
    kind = NotificationKind(
        key="admin_test",
        label="Admin test",
        description="For tests.",
        icon="info",
        render=lambda count, data: (f"{count} things", None),
        link=lambda data: None,
        admin_only=True,
    )
    monkeypatch.setitem(KINDS, kind.key, kind)
    return kind


# --- Registry ---

def test_every_kind_has_sample_data_and_renders():
    assert set(SAMPLE_DATA) == set(KINDS)
    for key, kind in KINDS.items():
        assert kind.key == key
        assert kind.label and kind.description
        title, body = kind.render(1, SAMPLE_DATA[key])
        assert title
        assert body is None or isinstance(body, str)
        title, _ = kind.render(3, SAMPLE_DATA[key])
        assert title
        link = kind.link(SAMPLE_DATA[key])
        assert link is None or link.startswith("/")


def test_every_kind_icon_exists_in_lucide_macro():
    macro = templates.env.get_template("partials/lucide_icon.html").module.lucide_icon
    for kind in KINDS.values():
        svg = str(macro(kind.icon))
        assert "<path" in svg or "<circle" in svg or "<rect" in svg, kind.icon


def test_whats_new_render_wording():
    render = KINDS["whats_new"].render
    assert render(1, {"version": "0.1.48", "first_title": "Notifications"}) == (
        "What's new in Parker 0.1.48",
        "Notifications",
    )
    assert render(4, {"version": "0.1.48", "first_title": "Notifications"})[1] == "Notifications, and 3 more"


def test_follow_arrivals_and_year_in_review_wording():
    follow = KINDS["follow_arrivals"]
    data = SAMPLE_DATA["follow_arrivals"]
    assert follow.render(1, data) == ("1 new issue in Saga Vol 1", None)
    assert follow.render(3, data) == ("3 new issues in Saga Vol 1", None)
    assert follow.link(data) == "/volumes/12"

    review = KINDS["year_in_review"]
    assert review.render(1, {"year": 2026}) == (
        "Your 2026 Year in Review is ready",
        "See your favorite series and reading milestones from 2026.",
    )
    assert review.link({"year": 2026}) == "/user/year-in-review?year=2026"


# --- notify() ---

def test_notify_unknown_kind_raises(db):
    with pytest.raises(ValueError):
        NotificationService(db).notify("nope", [1], data={})


def test_notify_creates_rendered_rows(db):
    user = _user(db, "reader")

    assert _notify(db, [user.id], count=2) == 1

    row = db.query(Notification).one()
    assert row.user_id == user.id
    assert row.kind == "whats_new"
    assert row.title == "What's new in Parker 0.1.48"
    assert row.body == "First, and 1 more"
    assert row.link == "/whats-new"
    assert row.count == 2
    assert row.read_at is None


def test_notify_skips_inactive_users_and_empty_lists(db):
    inactive = _user(db, "gone", active=False)

    assert _notify(db, []) == 0
    assert _notify(db, [inactive.id]) == 0
    assert db.query(Notification).count() == 0


def test_notify_does_nothing_when_feature_is_off(db):
    user = _user(db, "reader")
    _set_setting(db, "features.notifications_enabled", "false")

    assert _notify(db, [user.id]) == 0
    assert db.query(Notification).count() == 0


def test_notify_respects_user_preferences(db):
    muted = _user(db, "muted", preferences={"whats_new": {"in_app": False}})
    other_kind_muted = _user(db, "other", preferences={"something_else": {"in_app": False}})
    malformed = _user(db, "malformed", preferences={"whats_new": "off"})

    assert _notify(db, [muted.id, other_kind_muted.id, malformed.id]) == 2
    assert {row.user_id for row in db.query(Notification)} == {other_kind_muted.id, malformed.id}


def test_admin_only_kind_reaches_admins_only(db, test_kind):
    admin = _user(db, "admin", admin=True)
    reader = _user(db, "reader")

    assert NotificationService(db).notify(test_kind.key, [admin.id, reader.id], data={}) == 1
    assert db.query(Notification).one().user_id == admin.id


def test_grouping_updates_unread_and_starts_new_after_read(db):
    user = _user(db, "reader")
    service = NotificationService(db)

    _notify(db, [user.id], count=1, group_key="g", data={"version": "0.1.48", "first_title": "One"})
    _notify(db, [user.id], count=2, group_key="g", data={"version": "0.1.49", "first_title": "Two"})

    row = db.query(Notification).one()
    assert row.count == 3
    assert row.title == "What's new in Parker 0.1.49"
    assert row.body == "Two, and 2 more"
    assert row.data["first_title"] == "Two"

    service.mark_read(user.id, row.id)
    _notify(db, [user.id], count=1, group_key="g")

    assert db.query(Notification).count() == 2
    assert service.unread_count(user.id) == 1


def test_grouping_is_per_user_and_per_key(db):
    first = _user(db, "first")
    second = _user(db, "second")

    _notify(db, [first.id], group_key="a")
    _notify(db, [first.id, second.id], group_key="a")
    _notify(db, [first.id], group_key="b")

    counts = sorted((row.user_id, row.group_key, row.count) for row in db.query(Notification))
    assert counts == [(first.id, "a", 2), (first.id, "b", 1), (second.id, "a", 1)]


def test_notify_failure_is_logged_not_raised(db, monkeypatch, caplog):
    user = _user(db, "reader")
    broken = NotificationKind(
        key="whats_new",
        label="x",
        description="x",
        icon="info",
        render=lambda count, data: data["missing"],
        link=lambda data: None,
    )
    monkeypatch.setitem(KINDS, "whats_new", broken)

    assert _notify(db, [user.id]) == 0
    assert "Failed to write whats_new notifications" in caplog.text
    assert db.query(Notification).count() == 0


# --- Reading ---

def test_read_actions_are_scoped_to_the_user(db):
    owner = _user(db, "owner")
    other = _user(db, "other")
    _notify(db, [owner.id])
    _notify(db, [other.id])
    service = NotificationService(db)
    owner_row = db.query(Notification).filter_by(user_id=owner.id).one()

    assert service.mark_read(other.id, owner_row.id) is False
    assert service.delete(other.id, owner_row.id) is False
    assert service.unread_count(owner.id) == 1

    assert service.mark_read(owner.id, owner_row.id) is True
    assert service.unread_count(owner.id) == 0
    assert service.mark_read(owner.id, owner_row.id) is True  # already read is fine
    assert service.mark_all_read(other.id) == 1
    assert service.clear_read(other.id) == 1
    assert service.delete(owner.id, owner_row.id) is True
    assert db.query(Notification).count() == 0


def test_list_is_newest_first_with_unread_filter(db):
    user = _user(db, "reader")
    service = NotificationService(db)
    _notify(db, [user.id], group_key="old")
    _notify(db, [user.id], group_key="new")
    old = db.query(Notification).filter_by(group_key="old").one()
    old.updated_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    service.mark_read(user.id, old.id)

    total, rows = service.list_for_user(user.id)
    assert total == 2
    assert [row.group_key for row in rows] == ["new", "old"]

    total, rows = service.list_for_user(user.id, unread_only=True)
    assert total == 1
    assert rows[0].group_key == "new"

    total, rows = service.list_for_user(user.id, skip=1, limit=1)
    assert total == 2
    assert [row.group_key for row in rows] == ["old"]


def test_deleting_a_user_deletes_their_notifications(db):
    user = _user(db, "reader")
    _notify(db, [user.id])

    db.delete(user)
    db.commit()

    assert db.query(Notification).count() == 0


# --- Preferences ---

def test_preferences_default_on_and_update(db, test_kind):
    reader = _user(db, "reader")
    admin = _user(db, "admin", admin=True)
    service = NotificationService(db)

    reader_keys = ["follow_arrivals", "year_in_review", "whats_new"]
    assert [kind["key"] for kind in service.preferences_for(reader)] == reader_keys
    assert {kind["key"] for kind in service.preferences_for(admin)} == {*reader_keys, test_kind.key}
    assert all(kind["enabled"] for kind in service.preferences_for(reader))

    service.update_preferences(reader, {"whats_new": False})
    db.refresh(reader)
    assert reader.notification_preferences == {"whats_new": {"in_app": False}}
    enabled = {kind["key"]: kind["enabled"] for kind in service.preferences_for(reader)}
    assert enabled == {"follow_arrivals": True, "year_in_review": True, "whats_new": False}

    with pytest.raises(ValueError):
        service.update_preferences(reader, {test_kind.key: True})  # admin-only
    with pytest.raises(ValueError):
        service.update_preferences(reader, {"nope": True})


def test_update_preferences_keeps_other_channels(db):
    user = _user(db, "reader", preferences={"whats_new": {"webhook": True}})

    NotificationService(db).update_preferences(user, {"whats_new": False})
    db.refresh(user)

    assert user.notification_preferences == {"whats_new": {"webhook": True, "in_app": False}}


# --- Retention ---

def test_cleanup_deletes_old_read_and_very_old_unread(db):
    user = _user(db, "reader")
    now = datetime.now(timezone.utc)
    for group_key, age_days, read in (
        ("old-read", 31, True),
        ("recent-read", 5, True),
        ("old-unread", 91, False),
        ("month-unread", 60, False),
    ):
        _notify(db, [user.id], group_key=group_key)
        row = db.query(Notification).filter_by(group_key=group_key).one()
        row.updated_at = now - timedelta(days=age_days)
        row.read_at = now if read else None
    db.commit()

    assert NotificationService(db).cleanup_old_notifications(now=now) == 2
    assert {row.group_key for row in db.query(Notification)} == {"recent-read", "month-unread"}


def test_cleanup_uses_retention_setting(db):
    user = _user(db, "reader")
    _set_setting(db, "notifications.retention_days", "3", data_type="int")
    now = datetime.now(timezone.utc)
    _notify(db, [user.id])
    row = db.query(Notification).one()
    row.updated_at = now - timedelta(days=4)
    row.read_at = now
    db.commit()

    assert NotificationService(db).cleanup_old_notifications(now=now) == 1


def test_cleanup_runs_when_feature_is_off(db):
    user = _user(db, "reader")
    _notify(db, [user.id])
    row = db.query(Notification).one()
    row.updated_at = datetime.now(timezone.utc) - timedelta(days=100)
    db.commit()
    _set_setting(db, "features.notifications_enabled", "false")

    run_notification_checks(db)

    assert db.query(Notification).count() == 0


# --- What's New ---

def _entries():
    return [
        WhatsNewEntry(version="0.1.50", audience="all", title="Fifty"),
        WhatsNewEntry(version="0.1.49", audience="all", title="Forty-nine"),
        WhatsNewEntry(version="0.1.49", audience="admin", title="Admin forty-nine"),
        WhatsNewEntry(version="0.1.48", audience="all", title="Forty-eight"),
    ]


@pytest.fixture
def seeded_settings(db):
    SettingsService(db).initialize_defaults()
    return db


def _last_version(db) -> str:
    return SettingsService(db).get("notifications.whats_new_last_version")


def test_shipped_whats_new_file_is_valid():
    raw = json.loads(WHATS_NEW_FILE.read_text(encoding="utf-8"))
    assert len(load_whats_new_entries()) == len(raw)


def test_load_whats_new_skips_invalid_entries_and_sorts(tmp_path):
    path = tmp_path / "whats_new.json"
    path.write_text(json.dumps([
        {"version": "0.1.9", "title": "Nine"},
        {"version": "v0.1.10", "audience": "admin", "title": "Ten", "body": "Body", "link": "/admin"},
        {"version": "bad", "title": "Bad version"},
        {"version": "0.1.11", "audience": "everyone", "title": "Bad audience"},
        {"version": "0.1.12", "title": ""},
        "not an object",
    ]), encoding="utf-8")

    entries = load_whats_new_entries(path)

    assert [(entry.version, entry.title) for entry in entries] == [("0.1.10", "Ten"), ("0.1.9", "Nine")]
    assert entries[1].audience == "all"
    assert entries[0].link == "/admin"


def test_load_whats_new_handles_missing_and_broken_files(tmp_path):
    assert load_whats_new_entries(tmp_path / "missing.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert load_whats_new_entries(broken) == []


def test_whats_new_for_user_hides_unreleased_and_admin_entries():
    readers = whats_new_for_user(False, _entries(), current_version="0.1.49")
    admins = whats_new_for_user(True, _entries(), current_version="0.1.49")

    assert [entry.title for entry in readers] == ["Forty-nine", "Forty-eight"]
    assert [entry.title for entry in admins] == ["Forty-nine", "Admin forty-nine", "Forty-eight"]


def test_fresh_install_announces_nothing(seeded_settings):
    db = seeded_settings
    _user(db, "admin", admin=True)

    assert announce_whats_new(db, current_version="0.1.49", entries=_entries()) == 0
    assert db.query(Notification).count() == 0
    assert _last_version(db) == "0.1.49"


def test_first_run_on_existing_server_announces_current_version(seeded_settings, tmp_path):
    db = seeded_settings
    create_library_with_root(db, "Comics", str(tmp_path))
    reader = _user(db, "reader")

    assert announce_whats_new(db, current_version="0.1.49", entries=_entries()) == 1

    row = db.query(Notification).one()
    assert row.user_id == reader.id
    assert row.count == 1  # only the 0.1.49 reader entry, not 0.1.48
    assert row.body == "Forty-nine"


def test_upgrade_announces_skipped_versions_once_per_user(seeded_settings):
    db = seeded_settings
    SettingsService(db).update("notifications.whats_new_last_version", "0.1.47")
    reader = _user(db, "reader")
    admin = _user(db, "admin", admin=True)
    _user(db, "inactive", active=False)

    assert announce_whats_new(db, current_version="0.1.49", entries=_entries()) == 2

    rows = {row.user_id: row for row in db.query(Notification)}
    assert set(rows) == {reader.id, admin.id}
    assert rows[reader.id].count == 2
    assert rows[reader.id].title == "What's new in Parker 0.1.49"
    assert rows[reader.id].body == "Forty-nine, and 1 more"
    assert rows[admin.id].count == 3
    assert _last_version(db) == "0.1.49"

    # Restarting on the same version does nothing.
    assert announce_whats_new(db, current_version="0.1.49", entries=_entries()) == 0
    assert db.query(Notification).count() == 2


def test_upgrade_with_no_new_entries_still_records_version(seeded_settings):
    db = seeded_settings
    SettingsService(db).update("notifications.whats_new_last_version", "0.1.50")
    _user(db, "reader")

    assert announce_whats_new(db, current_version="0.1.51", entries=_entries()) == 0
    assert _last_version(db) == "0.1.51"


def test_admin_only_entries_do_not_notify_readers(seeded_settings):
    db = seeded_settings
    SettingsService(db).update("notifications.whats_new_last_version", "0.1.48")
    _user(db, "reader")
    entries = [WhatsNewEntry(version="0.1.49", audience="admin", title="Admin only")]

    assert announce_whats_new(db, current_version="0.1.49", entries=entries) == 0
    assert db.query(Notification).count() == 0


def test_announce_records_version_when_feature_is_off(seeded_settings):
    db = seeded_settings
    SettingsService(db).update("notifications.whats_new_last_version", "0.1.47")
    SettingsService(db).update("features.notifications_enabled", False)
    _user(db, "reader")

    assert announce_whats_new(db, current_version="0.1.49", entries=_entries()) == 0
    assert db.query(Notification).count() == 0
    assert _last_version(db) == "0.1.49"


def test_announce_skips_when_setting_row_is_missing(db):
    _user(db, "reader")
    assert announce_whats_new(db, current_version="0.1.49", entries=_entries()) == 0


# --- Hooks ---

def test_failing_startup_producer_does_not_stop_others(db, monkeypatch, caplog):
    calls = []

    def broken(session):
        raise RuntimeError("boom")

    monkeypatch.setattr(producers, "STARTUP_PRODUCERS", [broken, lambda session: calls.append(session)])

    run_startup_producers(db)

    assert calls == [db]
    assert "Notification startup step broken failed" in caplog.text


def test_daily_checks_receive_today(db, monkeypatch):
    calls = []
    monkeypatch.setattr(producers, "DAILY_CHECKS", [lambda session, today: calls.append(today)])

    run_notification_checks(db, today=datetime(2026, 12, 15).date())

    assert calls == [datetime(2026, 12, 15).date()]


def test_failing_scan_producer_does_not_stop_others(db, monkeypatch, caplog):
    calls = []
    scan = ScanInfo(library_id=1, started_at=SCAN_START, first_scan=False)

    def broken(session, scan_info):
        raise RuntimeError("boom")

    monkeypatch.setattr(producers, "SCAN_PRODUCERS", [broken, lambda session, scan_info: calls.append(scan_info)])

    run_scan_producers(db, scan)

    assert calls == [scan]
    assert "Notification scan step broken failed" in caplog.text


# --- Follow arrivals ---

SCAN_START = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _volume(db, library, series_name: str, *, volume_number: int = 1) -> Volume:
    series = Series(name=series_name, library=library)
    volume = Volume(series=series, volume_number=volume_number)
    db.add_all([series, volume])
    db.flush()
    return volume


def _issue(db, volume: Volume, number: str, *, created_at=None, **kwargs) -> Comic:
    return create_comic(
        db,
        volume,
        volume.series.library.active_root,
        f"{volume.series.name}-{volume.id}-{number}-{kwargs.get('format') or 'issue'}.cbz",
        number=number,
        filename=f"{volume.series.name}-{number}.cbz",
        created_at=created_at or SCAN_START + timedelta(minutes=5),
        **kwargs,
    )


def _follow(db, user: User, volume: Volume) -> None:
    db.add(UserVolumeFollow(user_id=user.id, volume_id=volume.id, followed_at=SCAN_START - timedelta(days=30)))
    db.commit()


def _scan(library, *, first_scan: bool = False) -> ScanInfo:
    return ScanInfo(library_id=library.id, started_at=SCAN_START, first_scan=first_scan)


@pytest.fixture
def follow_library(db):
    library = create_library_with_root(db, "follow-lib", "/tmp/follow-lib")
    db.commit()
    return library


def test_follow_arrivals_notifies_followers_once_per_volume(db, follow_library):
    reader = _user(db, "reader")
    reader.accessible_libraries.append(follow_library)
    other = _user(db, "other")
    other.accessible_libraries.append(follow_library)
    saga = _volume(db, follow_library, "Saga")
    paper = _volume(db, follow_library, "Paper Girls")
    _follow(db, reader, saga)
    _follow(db, reader, paper)
    _issue(db, saga, "1", created_at=SCAN_START - timedelta(days=1))  # From an earlier scan
    _issue(db, saga, "2")
    _issue(db, saga, "3")
    _issue(db, saga, "1", format="Annual")  # Not a plain issue
    _issue(db, paper, "5")
    db.commit()

    assert notify_follow_arrivals(db, _scan(follow_library)) == 2

    rows = {row.link: row for row in db.query(Notification).filter_by(user_id=reader.id)}
    assert rows[f"/volumes/{saga.id}"].title == "2 new issues in Saga Vol 1"
    assert rows[f"/volumes/{saga.id}"].group_key == f"follow:volume:{saga.id}"
    assert rows[f"/volumes/{paper.id}"].title == "1 new issue in Paper Girls Vol 1"
    assert db.query(Notification).filter_by(user_id=other.id).count() == 0


def test_follow_arrivals_groups_later_scans_until_read(db, follow_library):
    reader = _user(db, "reader", admin=True)
    saga = _volume(db, follow_library, "Saga")
    _follow(db, reader, saga)
    _issue(db, saga, "2")
    db.commit()
    notify_follow_arrivals(db, _scan(follow_library))

    later = SCAN_START + timedelta(days=7)
    _issue(db, saga, "3", created_at=later + timedelta(minutes=1))
    _issue(db, saga, "4", created_at=later + timedelta(minutes=1))
    db.commit()
    notify_follow_arrivals(db, ScanInfo(library_id=follow_library.id, started_at=later, first_scan=False))

    rows = db.query(Notification).filter_by(user_id=reader.id).all()
    assert len(rows) == 1
    assert rows[0].count == 3
    assert rows[0].title == "3 new issues in Saga Vol 1"


def test_follow_arrivals_respects_library_access_and_age_rating(db, follow_library):
    hidden_library = create_library_with_root(db, "hidden-lib", "/tmp/hidden-lib")
    no_access = _user(db, "no_access")
    no_access.accessible_libraries.append(hidden_library)
    teen = _user(db, "teen")
    teen.max_age_rating = "Teen"
    teen.accessible_libraries.append(follow_library)
    admin = _user(db, "admin", admin=True)
    mature = _volume(db, follow_library, "Mature Book")
    _issue(db, mature, "1", created_at=SCAN_START - timedelta(days=1), age_rating="Mature 17+")
    _issue(db, mature, "2", age_rating="Mature 17+")
    for user in (no_access, teen, admin):
        _follow(db, user, mature)

    assert notify_follow_arrivals(db, _scan(follow_library)) == 1
    assert [row.user_id for row in db.query(Notification)] == [admin.id]


def test_follow_arrivals_only_counts_the_scanned_library(db, follow_library):
    other_library = create_library_with_root(db, "other-lib", "/tmp/other-lib")
    admin = _user(db, "admin", admin=True)
    elsewhere = _volume(db, other_library, "Elsewhere")
    _follow(db, admin, elsewhere)
    _issue(db, elsewhere, "1")
    db.commit()

    assert notify_follow_arrivals(db, _scan(follow_library)) == 0


def test_follow_arrivals_skips_first_scan_and_bulk_imports(db, follow_library, monkeypatch, caplog):
    admin = _user(db, "admin", admin=True)
    saga = _volume(db, follow_library, "Saga")
    _follow(db, admin, saga)
    for number in range(1, 5):
        _issue(db, saga, str(number))
    db.commit()

    assert notify_follow_arrivals(db, _scan(follow_library, first_scan=True)) == 0

    monkeypatch.setattr(producers, "FOLLOW_ARRIVALS_BULK_LIMIT", 3)
    caplog.set_level("INFO")
    assert notify_follow_arrivals(db, _scan(follow_library)) == 0
    assert "4 new issues in 1 followed volumes looks like a bulk import" in caplog.text
    assert db.query(Notification).count() == 0

    monkeypatch.setattr(producers, "FOLLOW_ARRIVALS_BULK_LIMIT", 4)
    assert notify_follow_arrivals(db, _scan(follow_library)) == 1


def test_follow_arrivals_does_nothing_when_feature_is_off(db, follow_library):
    admin = _user(db, "admin", admin=True)
    saga = _volume(db, follow_library, "Saga")
    _follow(db, admin, saga)
    _issue(db, saga, "1")
    _set_setting(db, "features.notifications_enabled", "false")

    assert notify_follow_arrivals(db, _scan(follow_library)) == 0
    assert db.query(Notification).count() == 0


# --- Year in Review ---

def _finished(db, user: User, volume: Volume, read_at: datetime, *, completed: bool = True) -> None:
    comic = _issue(db, volume, f"r{db.query(Comic).count() + 1}")
    db.add(ReadingProgress(
        user_id=user.id,
        comic_id=comic.id,
        current_page=19 if completed else 3,
        total_pages=20,
        completed=completed,
        last_read_at=read_at,
    ))
    db.commit()


@pytest.fixture
def review_readers(seeded_settings, follow_library):
    db = seeded_settings
    volume = _volume(db, follow_library, "Saga")
    reader = _user(db, "reader")
    _finished(db, reader, volume, datetime(2026, 3, 1))
    _finished(db, reader, volume, datetime(2026, 4, 1))
    last_year_only = _user(db, "last_year_only")
    _finished(db, last_year_only, volume, datetime(2025, 6, 1))
    unfinished = _user(db, "unfinished")
    _finished(db, unfinished, volume, datetime(2026, 5, 1), completed=False)
    inactive = _user(db, "inactive", active=False)
    _finished(db, inactive, volume, datetime(2026, 5, 1))
    return {"reader": reader, "last_year_only": last_year_only, "volume": volume}


def _review_year(db) -> int:
    return SettingsService(db).get("notifications.year_in_review_last_year")


def test_year_in_review_season_dates():
    assert year_in_review_year(date(2026, 12, 14)) is None
    assert year_in_review_year(date(2026, 12, 15)) == 2026
    assert year_in_review_year(date(2026, 12, 31)) == 2026
    assert year_in_review_year(date(2027, 1, 31)) == 2026
    assert year_in_review_year(date(2027, 2, 1)) is None
    assert year_in_review_year(date(2026, 6, 1)) is None


def test_year_in_review_waits_for_december_15(db, review_readers):
    assert announce_year_in_review(db, date(2026, 12, 14)) == 0
    assert db.query(Notification).count() == 0
    assert _review_year(db) == 0


def test_year_in_review_sends_once_to_users_who_finished_a_comic(db, review_readers):
    reader = review_readers["reader"]

    assert announce_year_in_review(db, date(2026, 12, 15)) == 1

    rows = db.query(Notification).all()
    assert [row.user_id for row in rows] == [reader.id]
    assert rows[0].title == "Your 2026 Year in Review is ready"
    assert rows[0].link == "/user/year-in-review?year=2026"
    assert _review_year(db) == 2026

    assert announce_year_in_review(db, date(2026, 12, 16)) == 0
    assert announce_year_in_review(db, date(2027, 1, 5)) == 0
    assert db.query(Notification).count() == 1


def test_year_in_review_january_run_covers_a_missed_december(db, review_readers):
    assert announce_year_in_review(db, date(2026, 1, 10)) == 1
    assert [row.user_id for row in db.query(Notification)] == [review_readers["last_year_only"].id]
    assert _review_year(db) == 2025


def test_year_in_review_not_sent_after_january(db, review_readers):
    assert announce_year_in_review(db, date(2027, 2, 1)) == 0
    assert db.query(Notification).count() == 0


def test_year_in_review_waits_while_feature_is_off(db, review_readers):
    _set_setting(db, "features.notifications_enabled", "false")
    assert announce_year_in_review(db, date(2026, 12, 15)) == 0
    assert _review_year(db) == 0

    _set_setting(db, "features.notifications_enabled", "true")
    assert announce_year_in_review(db, date(2026, 12, 20)) == 1


def test_year_in_review_skips_when_setting_row_is_missing(db, follow_library):
    volume = _volume(db, follow_library, "Saga")
    reader = _user(db, "reader")
    _finished(db, reader, volume, datetime(2026, 3, 1))

    assert announce_year_in_review(db, date(2026, 12, 15)) == 0


def test_daily_check_sends_year_in_review(db, review_readers):
    run_notification_checks(db, today=date(2026, 12, 15))
    assert db.query(Notification).filter_by(kind="year_in_review").count() == 1
