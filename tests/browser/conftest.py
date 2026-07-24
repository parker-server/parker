from __future__ import annotations

import base64
import socket
from pathlib import Path
from threading import Thread
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.core.security import get_password_hash
from app.database import Base
from app.main import app
from app.models.bookmark import Bookmark
from app.models.comic import Comic, Volume
from app.models.credits import ComicCredit, Person
from app.models.interactions import UserLibraryPin, UserVolumeFollow
from app.models.library import Library
from app.models.reading_progress import ReadingProgress
from app.models.reading_list import ReadingList, ReadingListItem
from app.models.pull_list import PullList, PullListItem
from app.models.saved_search import SavedSearch
from app.models.series import Series
from app.models.tags import Character
from app.models.user import User

from tests.factories import create_library_with_root

PNG_PIXEL_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9sZVE70AAAAASUVORK5CYII="
)


class BrowserImageService:
    def get_page_image(self, file_path, page_index, sharpen=False, grayscale=False, transcode_webp=False):
        return PNG_PIXEL_BYTES, True, "image/png"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def browser_db_factory(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("browser-db") / "browser-tests.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)
    yield session_local
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="session")
def browser_seed_data(browser_db_factory):
    session = browser_db_factory()
    library = create_library_with_root(session, "Browser Test Library", str(Path("/tmp/browser-test-library")))
    root = library.active_root
    series = Series(name="Smoke Series", library=library)
    volume = Volume(series=series, volume_number=1)
    completed_comic = Comic(
        volume=volume,
        library_root_id=root.id,
        relative_path="smoke-reader.cbz",
        number="1",
        title="Smoke Reader",
        filename="smoke-reader.cbz",
        page_count=3,
    )
    active_comic = Comic(
        volume=volume,
        library_root_id=root.id,
        relative_path="smoke-encore.cbz",
        number="2",
        title="Smoke Encore",
        filename="smoke-encore.cbz",
        page_count=3,
    )
    in_progress_comic = Comic(
        volume=volume,
        library_root_id=root.id,
        relative_path="smoke-horizon.cbz",
        number="3",
        title="Smoke Horizon",
        year=2024,
        month=5,
        filename="smoke-horizon.cbz",
        page_count=4,
    )
    reading_list = ReadingList(
        name="Smoke Crossover",
        description="A compact browser-test event list.",
        auto_generated=0,
    )
    writer = Person(name="Casey Smoke")
    character = Character(name="Captain Smoke")
    user = User(
        username="browser-user",
        email="browser@example.com",
        hashed_password=get_password_hash("browser-pass"),
        is_superuser=False,
        is_active=True,
    )
    user.accessible_libraries.append(library)
    in_progress_comic.credits.append(ComicCredit(person=writer, role="writer"))
    in_progress_comic.characters.append(character)
    reading_list.items.extend(
        [
            ReadingListItem(comic=active_comic, position=1),
            ReadingListItem(comic=completed_comic, position=2),
            ReadingListItem(comic=in_progress_comic, position=3),
        ]
    )

    session.add_all(
        [
            library,
            series,
            volume,
            completed_comic,
            active_comic,
            in_progress_comic,
            reading_list,
            writer,
            character,
            user,
        ]
    )
    session.commit()
    session.refresh(completed_comic)
    session.refresh(active_comic)
    session.refresh(in_progress_comic)
    session.refresh(reading_list)
    session.refresh(user)

    completed_progress = ReadingProgress(
        user_id=user.id,
        comic_id=completed_comic.id,
        current_page=completed_comic.page_count - 1,
        total_pages=completed_comic.page_count,
        completed=True,
    )
    session.add(completed_progress)
    session.commit()

    data = {
        "user_id": user.id,
        "series_id": series.id,
        "volume_id": volume.id,
        "series_name": series.name,
        "reading_list_id": reading_list.id,
        "reading_list_name": reading_list.name,
        "completed_comic_id": completed_comic.id,
        "completed_comic_title": completed_comic.title,
        "completed_comic_number": completed_comic.number,
        "active_comic_id": active_comic.id,
        "active_comic_title": active_comic.title,
        "active_comic_number": active_comic.number,
        "in_progress_comic_id": in_progress_comic.id,
        "in_progress_comic_title": in_progress_comic.title,
        "in_progress_comic_number": in_progress_comic.number,
    }

    session.close()
    return data


@pytest.fixture(scope="function", autouse=True)
def reset_browser_state(browser_db_factory, browser_seed_data):
    session = browser_db_factory()
    try:
        session.query(UserVolumeFollow).delete()
        session.query(UserLibraryPin).delete()
        session.query(PullListItem).delete()
        session.query(PullList).delete()
        session.query(SavedSearch).delete()
        session.query(Bookmark).delete()
        session.query(ReadingProgress).delete()
        user = session.scalar(select(User).where(User.id == browser_seed_data["user_id"]))
        if user is not None:
            user.social_insights_enabled = True
        session.add_all(
            [
                ReadingProgress(
                    user_id=browser_seed_data["user_id"],
                    comic_id=browser_seed_data["completed_comic_id"],
                    current_page=2,
                    total_pages=3,
                    completed=True,
                ),
                ReadingProgress(
                    user_id=browser_seed_data["user_id"],
                    comic_id=browser_seed_data["in_progress_comic_id"],
                    current_page=1,
                    total_pages=4,
                    completed=False,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture(scope="session")
def browser_server(browser_db_factory, browser_seed_data, monkeypatch_session):
    def override_get_db():
        session = browser_db_factory()
        try:
            yield session
        finally:
            session.close()

    def override_get_current_user():
        session = browser_db_factory()
        try:
            user = session.scalar(
                select(User)
                .options(selectinload(User.accessible_libraries).selectinload(Library.roots))
                .where(User.id == browser_seed_data["user_id"])
            )
            _ = list(user.accessible_libraries)
            session.expunge(user)
            return user
        finally:
            session.close()

    monkeypatch_session.setattr("app.api.reader.ImageService", BrowserImageService)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=base_url, timeout=2.0) as client:
        for _ in range(40):
            try:
                response = client.get("/health")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            import time
            time.sleep(0.1)
        else:
            server.should_exit = True
            thread.join(timeout=5)
            pytest.fail("Timed out waiting for browser test server to start")

    yield {
        "base_url": base_url,
        "db_factory": browser_db_factory,
        "seed": browser_seed_data,
    }

    server.should_exit = True
    thread.join(timeout=5)
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def monkeypatch_session():
    monkeypatch = pytest.MonkeyPatch()
    yield monkeypatch
    monkeypatch.undo()


@pytest.fixture(scope="session")
def playwright_module() -> Any:
    return pytest.importorskip("playwright.sync_api", reason="Install playwright to run browser tests")


@pytest.fixture(scope="session")
def browser(playwright_module):
    try:
        with playwright_module.sync_playwright() as runner:
            chromium = runner.chromium.launch(headless=True)
            yield chromium
            chromium.close()
    except Exception as exc:  # pragma: no cover - depends on host browser install
        pytest.skip(f"Playwright browser launch failed: {exc}")


@pytest.fixture(scope="function")
def page(browser, browser_server):
    context = browser.new_context(viewport={"width": 1440, "height": 1200})
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="function")
def mobile_page(browser, browser_server):
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()
    yield page
    context.close()
