import pytest

import app.services.scanner as scanner_module
from app.models.comic import Volume
from app.models.notification import Notification
from app.models.problem_report import ProblemReport
from app.models.reading_list import ReadingList
from app.models.series import Series
from app.models.setting import SystemSetting
from app.models.user import User
from app.services.problem_reports import (
    ProblemReportError,
    ProblemReportService,
    user_can_report,
)
from app.services.scanner import LibraryScanner
from tests.factories import create_comic, create_library_with_root


def _seed_comic(db, *, name="Saga", number="12", page_count=20, path=None):
    library = create_library_with_root(db, f"{name}-lib", path or f"/tmp/{name}-lib")
    series = Series(name=name, library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()
    comic = create_comic(
        db,
        volume,
        library.active_root,
        f"{name}/{name} {number}.cbz",
        number=number,
        filename=f"{name} {number}.cbz",
        page_count=page_count,
    )
    db.commit()
    return comic


def _make_user(db, username, *, admin=False):
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="fakehash",
        is_superuser=admin,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _set_setting(db, key, value):
    db.add(SystemSetting(key=key, value=value, category="features", data_type="bool"))
    db.commit()


def test_submit_creates_report_with_label_and_notifies_admins(db, normal_user, admin_user):
    comic = _seed_comic(db)

    report, created = ProblemReportService(db).submit(
        normal_user, comic, "missing_pages", page_index=4, message="  Pages 5-6 missing  "
    )

    assert created is True
    assert report.status == "open"
    assert report.page_index == 4
    assert report.message == "Pages 5-6 missing"
    assert report.comic_label == "Saga #12 (Saga/Saga 12.cbz)"

    notification = db.query(Notification).filter(Notification.user_id == admin_user.id).one()
    assert notification.kind == "admin_problem_new"
    assert notification.title == "New problem report: Missing pages in Saga #12"
    assert notification.link == "/admin/problems"
    # The reporter is not an admin, so they get nothing.
    assert db.query(Notification).filter(Notification.user_id == normal_user.id).count() == 0


def test_submit_again_updates_open_report_instead_of_adding(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    first, _ = service.submit(normal_user, comic, "scan_quality", page_index=1)
    first.file_changed_at = first.created_at
    db.commit()

    second, created = service.submit(normal_user, comic, "scan_quality", page_index=7, message="Still blurry")

    assert created is False
    assert second.id == first.id
    assert second.page_index == 7
    assert second.message == "Still blurry"
    # Reporting again means the file change did not fix it.
    assert second.file_changed_at is None
    assert db.query(ProblemReport).count() == 1
    # Admins hear about new reports only, grouped in one notification.
    assert db.query(Notification).filter(Notification.user_id == admin_user.id).one().count == 1


def test_different_category_or_closed_report_adds_a_new_one(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    first, _ = service.submit(normal_user, comic, "scan_quality")
    service.submit(normal_user, comic, "metadata")
    service.apply([first.id], "resolve", admin_user)

    _, created = service.submit(normal_user, comic, "scan_quality")

    assert created is True
    assert db.query(ProblemReport).count() == 3


def test_submit_validation(db, normal_user):
    comic = _seed_comic(db, page_count=10)
    service = ProblemReportService(db)

    with pytest.raises(ProblemReportError):
        service.submit(normal_user, comic, "not-a-category")
    with pytest.raises(ProblemReportError, match="describe"):
        service.submit(normal_user, comic, "other", message="   ")
    with pytest.raises(ProblemReportError, match="out of range"):
        service.submit(normal_user, comic, "broken", page_index=10)
    with pytest.raises(ProblemReportError, match="1000"):
        service.submit(normal_user, comic, "broken", message="x" * 1001)

    # Pages are not stored for categories where they don't help.
    report, _ = service.submit(normal_user, comic, "metadata", page_index=3)
    assert report.page_index is None


def test_withdraw_only_own_open_reports(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    mine, _ = service.submit(normal_user, comic, "broken")
    theirs, _ = service.submit(admin_user, comic, "broken")
    closed, _ = service.submit(normal_user, comic, "metadata")
    service.apply([closed.id], "dismiss", admin_user)

    assert service.withdraw(normal_user.id, theirs.id) is False
    assert service.withdraw(normal_user.id, closed.id) is False
    assert service.withdraw(normal_user.id, mine.id) is True
    assert db.get(ProblemReport, mine.id) is None
    assert db.get(ProblemReport, theirs.id) is not None
    assert db.get(ProblemReport, closed.id) is not None


def test_resolve_with_note_notifies_reporter(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    report, _ = service.submit(normal_user, comic, "missing_pages")

    changed = service.apply([report.id], "resolve", admin_user, note="Replaced the file")

    assert [r.id for r in changed] == [report.id]
    db.refresh(report)
    assert report.status == "resolved"
    assert report.admin_note == "Replaced the file"
    assert report.resolved_by_id == admin_user.id
    assert report.resolved_at is not None

    notification = db.query(Notification).filter(Notification.user_id == normal_user.id).one()
    assert notification.kind == "problem_resolved"
    assert notification.title == "Your report on Saga #12 was resolved"
    assert notification.body == "Replaced the file"
    assert notification.link == "/user/problems"


def test_batch_dismiss_sends_one_notification_per_reporter(db, normal_user, admin_user):
    service = ProblemReportService(db)
    reports = [
        service.submit(normal_user, _seed_comic(db, name=f"Series{index}"), "broken")[0]
        for index in range(3)
    ]

    service.apply([report.id for report in reports], "dismiss", admin_user)

    notification = db.query(Notification).filter(Notification.user_id == normal_user.id).one()
    assert notification.count == 3
    assert notification.title == "3 of your problem reports were closed"


def test_admin_resolving_own_report_is_not_notified(db, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    report, _ = service.submit(admin_user, comic, "broken")

    service.apply([report.id], "resolve", admin_user)

    assert db.query(Notification).count() == 0


def test_reopen_clears_resolution_and_file_hint(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    report, _ = service.submit(normal_user, comic, "broken")
    service.mark_file_changed([comic.id])
    service.apply([report.id], "resolve", admin_user, note="Fixed")

    changed = service.apply([report.id], "reopen", admin_user)

    assert len(changed) == 1
    db.refresh(report)
    assert report.status == "open"
    assert report.resolved_at is None
    assert report.resolved_by_id is None
    assert report.file_changed_at is None
    # The note stays so the admin can see what was tried.
    assert report.admin_note == "Fixed"
    # Reopening an open report is a no-op.
    assert service.apply([report.id], "reopen", admin_user) == []


def test_unknown_action_is_refused(db, admin_user):
    with pytest.raises(ProblemReportError):
        ProblemReportService(db).apply([1], "delete", admin_user)


def test_queue_groups_reports_by_comic_category_and_status(db, normal_user, admin_user):
    comic = _seed_comic(db)
    other_comic = _seed_comic(db, name="Paper Girls", number="3")
    second_reader = _make_user(db, "second-reader")
    service = ProblemReportService(db)
    service.submit(normal_user, comic, "missing_pages", page_index=2, message="Missing 3")
    service.submit(second_reader, comic, "missing_pages", page_index=8)
    service.submit(normal_user, comic, "metadata")
    service.submit(normal_user, other_comic, "broken")

    groups = service.queue()

    assert len(groups) == 3
    missing = next(group for group in groups if group["category"] == "missing_pages")
    assert missing["count"] == 2
    assert missing["comic_name"] == "Saga #12"
    assert missing["category_label"] == "Missing pages"
    # The newest report's page is shown.
    assert missing["page_index"] == 8
    assert {report["reporter"] for report in missing["reports"]} == {"testuser", "second-reader"}
    assert len(missing["report_ids"]) == 2

    assert [group["category"] for group in service.queue(category="broken")] == ["broken"]
    assert service.queue(status="closed") == []
    assert service.open_count() == 4


def test_mark_file_changed_flags_only_open_reports(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    open_report, _ = service.submit(normal_user, comic, "broken")
    closed_report, _ = service.submit(normal_user, comic, "metadata")
    service.apply([closed_report.id], "resolve", admin_user)

    assert service.mark_file_changed([comic.id, 9999]) == 1

    db.refresh(open_report)
    db.refresh(closed_report)
    assert open_report.file_changed_at is not None
    assert closed_report.file_changed_at is None
    groups = service.queue(file_changed_only=True)
    assert len(groups) == 1 and groups[0]["file_changed"] is True


def test_deleting_comic_keeps_report_with_label(db, normal_user):
    comic = _seed_comic(db)
    report, _ = ProblemReportService(db).submit(normal_user, comic, "wrong_comic")

    db.delete(comic)
    db.commit()
    db.expire_all()

    kept = db.get(ProblemReport, report.id)
    assert kept is not None
    assert kept.comic_id is None
    group = ProblemReportService(db).queue()[0]
    assert group["comic_removed"] is True
    assert group["comic_name"] == "Saga #12 (Saga/Saga 12.cbz)"


def test_deleting_reporter_keeps_their_reports_without_a_user(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    reader_report, _ = service.submit(normal_user, comic, "broken", message="Page 4 is blank")
    admin_report, _ = service.submit(admin_user, comic, "metadata")
    other_admin = _make_user(db, "other-admin", admin=True)
    service.apply([admin_report.id], "resolve", other_admin)

    db.delete(normal_user)
    db.delete(other_admin)
    db.commit()
    db.expire_all()

    kept_open = db.get(ProblemReport, reader_report.id)
    assert kept_open is not None
    assert kept_open.user_id is None
    assert kept_open.message == "Page 4 is blank"
    kept_closed = db.get(ProblemReport, admin_report.id)
    assert kept_closed is not None
    assert kept_closed.resolved_by_id is None

    group = next(group for group in service.queue() if group["category"] == "broken")
    assert group["reports"][0]["reporter"] is None


def test_resolving_a_deleted_users_report_notifies_nobody(db, normal_user, admin_user):
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    report, _ = service.submit(normal_user, comic, "broken")
    db.delete(normal_user)
    db.commit()
    db.query(Notification).delete()
    db.commit()

    service.apply([report.id], "resolve", admin_user, note="Fixed")

    assert db.get(ProblemReport, report.id).status == "resolved"
    assert db.query(Notification).count() == 0


def test_resolving_while_the_feature_is_off_notifies_nobody(db, normal_user, admin_user):
    # With reports off, My Reports is a 404, so a notification would lead nowhere.
    comic = _seed_comic(db)
    service = ProblemReportService(db)
    report, _ = service.submit(normal_user, comic, "broken")
    db.query(Notification).delete()
    db.commit()
    _set_setting(db, "features.problem_reports_enabled", "false")

    service.apply([report.id], "resolve", admin_user, note="Fixed")

    assert db.get(ProblemReport, report.id).status == "resolved"
    assert db.query(Notification).count() == 0


def test_list_for_user_hides_comics_the_user_can_no_longer_see(db, normal_user):
    visible = _seed_comic(db, name="Visible")
    hidden = _seed_comic(db, name="Hidden")
    normal_user.accessible_libraries.extend([visible.volume.series.library, hidden.volume.series.library])
    db.commit()
    service = ProblemReportService(db)
    service.submit(normal_user, visible, "broken")
    service.submit(normal_user, hidden, "broken")
    removed = _seed_comic(db, name="Removed")
    service.submit(normal_user, removed, "broken")
    db.delete(removed)

    normal_user.accessible_libraries.remove(hidden.volume.series.library)
    db.commit()

    names = [report.comic_label for report in service.list_for_user(normal_user)]
    assert len(names) == 2
    assert not any(name.startswith("Hidden") for name in names)


def test_user_can_report_respects_setting_and_user_flag(db, normal_user, admin_user):
    assert user_can_report(normal_user, db) is True

    normal_user.can_report = False
    admin_user.can_report = False
    db.commit()
    assert user_can_report(normal_user, db) is False
    assert user_can_report(admin_user, db) is True

    _set_setting(db, "features.problem_reports_enabled", "false")
    assert user_can_report(admin_user, db) is False


def _scanner_for(db, tmp_path):
    library = create_library_with_root(db, "scan-problems", str(tmp_path))
    series = Series(name="Scanned", library=library)
    volume = Volume(series=series, volume_number=1)
    db.add_all([series, volume])
    db.flush()

    changed_file = tmp_path / "changed.cbz"
    unchanged_file = tmp_path / "unchanged.cbz"
    changed_file.write_bytes(b"x")
    unchanged_file.write_bytes(b"x")
    changed = create_comic(
        db, volume, library.active_root, "changed.cbz",
        number="1", filename="changed.cbz", page_count=5,
        file_modified_at=changed_file.stat().st_mtime - 60,
    )
    unchanged = create_comic(
        db, volume, library.active_root, "unchanged.cbz",
        number="2", filename="unchanged.cbz", page_count=5,
        file_modified_at=unchanged_file.stat().st_mtime + 60,
    )
    db.commit()

    scanner = LibraryScanner(library, db)
    scanner.reading_list_service.cleanup_empty_lists = lambda: None
    scanner.collection_service.cleanup_empty_collections = lambda: None
    return scanner, changed, unchanged


class _FakeQueue:
    def __init__(self, items=None):
        self._items = list(items or [])

    def put(self, item):
        pass

    def get(self, timeout=None):
        return self._items.pop(0)


class _FakeProcess:
    def __init__(self, *args, **kwargs):
        self.alive = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.alive = False


class _FakePool:
    def __init__(self, processes):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def imap_unordered(self, worker_fn, tasks):
        return [{"file_path": task["file_path"]} for task in tasks]


def _patch_scanner(monkeypatch, summary):
    queues = [_FakeQueue(), _FakeQueue([summary])]
    monkeypatch.setattr(scanner_module, "Queue", lambda: queues.pop(0))
    monkeypatch.setattr(scanner_module.multiprocessing, "Process", _FakeProcess)
    monkeypatch.setattr(scanner_module.multiprocessing, "Pool", _FakePool)
    monkeypatch.setattr(scanner_module, "get_cached_setting", lambda _key, default=0: default)


@pytest.mark.parametrize("force", [False, True])
def test_scan_flags_open_reports_only_for_changed_files(monkeypatch, db, tmp_path, normal_user, force):
    scanner, changed, unchanged = _scanner_for(db, tmp_path)
    service = ProblemReportService(db)
    changed_report, _ = service.submit(normal_user, changed, "scan_quality")
    unchanged_report, _ = service.submit(normal_user, unchanged, "scan_quality")
    _patch_scanner(monkeypatch, {"summary": True, "imported": 0, "updated": 1, "errors": 0, "skipped": 0})

    scanner.scan_parallel(force=force, worker_limit=1)

    db.refresh(changed_report)
    db.refresh(unchanged_report)
    assert changed_report.status == "open"
    assert changed_report.file_changed_at is not None
    # A forced rescan of an unchanged file is not a fix.
    assert unchanged_report.file_changed_at is None


def test_scan_survives_problem_report_hook_failure(monkeypatch, db, tmp_path, normal_user):
    scanner, changed, _ = _scanner_for(db, tmp_path)
    ProblemReportService(db).submit(normal_user, changed, "broken")
    # The scan's own end-of-scan cleanup must not be undone by the hook failing,
    # so use the real empty-list cleanup that _scanner_for stubs out.
    del scanner.reading_list_service.cleanup_empty_lists
    db.add(ReadingList(name="Empty ComicInfo List", source="comicinfo"))
    db.commit()
    _patch_scanner(monkeypatch, {"summary": True, "imported": 0, "updated": 1, "errors": 0, "skipped": 0})

    def _explode(self, comic_ids):
        raise RuntimeError("boom")

    monkeypatch.setattr(scanner_module.ProblemReportService, "mark_file_changed", _explode)

    result = scanner.scan_parallel(worker_limit=1)

    assert result["fatal_error"] is None
    assert scanner.library.last_scanned is not None
    db.expire_all()
    assert db.query(ReadingList).filter(ReadingList.name == "Empty ComicInfo List").count() == 0
