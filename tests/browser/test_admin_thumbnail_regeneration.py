import pytest

from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.library_root import LibraryRoot
from app.models.series import Series
from app.models.user import User
from app.services.scan_manager import scan_manager
from tests.factories import create_comic, create_library_with_root


def _record_thumbnail_tasks(monkeypatch) -> list[tuple[int, bool]]:
    calls: list[tuple[int, bool]] = []

    def fake_add_thumbnail_task(library_id: int, force: bool = False) -> dict:
        calls.append((library_id, force))
        return {"status": "queued", "job_id": len(calls), "message": "Job queued"}

    monkeypatch.setattr(scan_manager, "add_thumbnail_task", fake_add_thumbnail_task)
    return calls


def _set_superuser(browser_server, value: bool) -> None:
    session = browser_server["db_factory"]()
    try:
        session.get(User, browser_server["seed"]["user_id"]).is_superuser = value
        session.commit()
    finally:
        session.close()


def _delete_library(browser_server, library_id: int) -> None:
    session = browser_server["db_factory"]()
    try:
        volume_ids = [
            volume_id for (volume_id,) in session.query(Volume.id).join(Series).filter(Series.library_id == library_id)
        ]
        if volume_ids:
            session.query(Comic).filter(Comic.volume_id.in_(volume_ids)).delete(synchronize_session=False)
            session.query(Volume).filter(Volume.id.in_(volume_ids)).delete(synchronize_session=False)
        session.query(Series).filter(Series.library_id == library_id).delete(synchronize_session=False)
        session.query(LibraryRoot).filter(LibraryRoot.library_id == library_id).delete(synchronize_session=False)
        session.query(Library).filter(Library.id == library_id).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


@pytest.mark.browser
def test_library_thumbnails_button_queues_forced_job_for_that_library(page, browser_server, tmp_path, monkeypatch):
    calls = _record_thumbnail_tasks(monkeypatch)
    name = f"Thumbnail UI {tmp_path.name}"

    session = browser_server["db_factory"]()
    try:
        library = create_library_with_root(session, name, str(tmp_path / "thumbnail-ui"))
        series = Series(name="Thumbnail UI Series", library=library)
        volume = Volume(series=series, volume_number=1)
        session.add_all([series, volume])
        session.flush()
        for number in ("1", "2"):
            create_comic(
                session, volume, library.active_root, f"issue-{number}.cbz",
                filename=f"issue-{number}.cbz", number=number,
            )
        session.commit()
        library_id = library.id
    finally:
        session.close()
    _set_superuser(browser_server, True)

    try:
        page.goto(f"{browser_server['base_url']}/admin/libraries", wait_until="networkidle")
        page.get_by_label("Filter libraries").fill(name)
        row = page.get_by_role("row").filter(has_text=name)
        row.get_by_role("button", name="Thumbnails").click()

        dialog = page.locator('[x-data="globalDialog()"]')
        dialog.get_by_text(f'all 2 comics in "{name}"').wait_for()
        dialog.get_by_role("button", name="Regenerate").click()

        page.get_by_text("Thumbnail regeneration started").wait_for()
        assert calls == [(library_id, True)]
    finally:
        _set_superuser(browser_server, False)
        _delete_library(browser_server, library_id)


@pytest.mark.browser
def test_tasks_card_runs_changed_only_and_all_thumbnail_jobs(page, browser_server, tmp_path, monkeypatch):
    calls = _record_thumbnail_tasks(monkeypatch)

    session = browser_server["db_factory"]()
    try:
        library_id = create_library_with_root(session, f"Tasks Thumbs {tmp_path.name}", str(tmp_path / "tasks")).id
        session.commit()
        library_count = session.query(Library).count()
    finally:
        session.close()
    _set_superuser(browser_server, True)

    try:
        page.goto(f"{browser_server['base_url']}/admin/tasks", wait_until="networkidle")
        page.get_by_role("heading", name="Regenerate Thumbnails").wait_for()
        assert page.get_by_role("heading", name="Refresh Colorscapes").count() == 0

        page.get_by_role("button", name="Changed only").click()
        page.get_by_text("Background job(s) started").wait_for()
        assert len(calls) == library_count
        assert all(force is False for _, force in calls)

        calls.clear()
        page.get_by_role("button", name="All", exact=True).click()
        dialog = page.locator('[x-data="globalDialog()"]')
        dialog.get_by_text("every comic in every library").wait_for()
        dialog.get_by_role("button", name="Regenerate All").click()

        page.get_by_text("Background job(s) started").wait_for()
        assert len(calls) == library_count
        assert all(force is True for _, force in calls)
    finally:
        _set_superuser(browser_server, False)
        _delete_library(browser_server, library_id)
