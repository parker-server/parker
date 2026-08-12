import zipfile
from xml.etree import ElementTree as ET

import pytest

from app.models.comic import Comic
from app.models.library_root import LibraryRoot
from app.models.tags import Genre
from app.models.user import User


def _write_cbz(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _comicinfo_values(path):
    with zipfile.ZipFile(path, "r") as archive:
        root = ET.fromstring(archive.read("ComicInfo.xml"))
        return {child.tag: child.text for child in root}


def _prepare_editable_archive(browser_server, tmp_path):
    seed = browser_server["seed"]
    session = browser_server["db_factory"]()
    try:
        user = session.get(User, seed["user_id"])
        comic = session.get(Comic, seed["active_comic_id"])
        root = comic.library_root
        suggested_genre = session.query(Genre).filter(Genre.name == "Browser Suggested Genre").first()
        if suggested_genre is None:
            suggested_genre = Genre(name="Browser Suggested Genre")
            session.add(suggested_genre)
            session.flush()
        if suggested_genre not in comic.genres:
            comic.genres.append(suggested_genre)

        archive_path = tmp_path / comic.relative_path
        _write_cbz(
            archive_path,
            {
                "pages/001.jpg": b"page-one",
                "ComicInfo.xml": b"""<ComicInfo>
                    <Series>Smoke Series</Series>
                    <Number>2</Number>
                    <Volume>1</Volume>
                    <Title>Smoke Encore</Title>
                    <Summary>Original browser-test summary.</Summary>
                    <Year>2024</Year>
                    <Writer>Casey Smoke</Writer>
                    <Penciller>Pat Pencil</Penciller>
                    <Publisher>Original Publisher</Publisher>
                    <Imprint>Original Imprint</Imprint>
                    <Count>5</Count>
                    <AlternateSeries>Original Event</AlternateSeries>
                    <AlternateNumber>1</AlternateNumber>
                    <Genre>Original Genre</Genre>
                    <Format>Annual</Format>
                    <AgeRating>Everyone</AgeRating>
                </ComicInfo>""",
            },
        )

        original_state = {
            "root_id": root.id,
            "root_path": root.path,
            "user_id": user.id,
            "is_superuser": user.is_superuser,
            "archive_path": archive_path,
            "library_id": root.library_id,
        }

        root.path = str(tmp_path)
        user.is_superuser = True
        session.commit()

        return original_state
    finally:
        session.close()


def _restore_editable_archive_state(browser_server, original_state):
    session = browser_server["db_factory"]()
    try:
        root = session.get(LibraryRoot, original_state["root_id"])
        user = session.get(User, original_state["user_id"])

        root.path = original_state["root_path"]
        user.is_superuser = original_state["is_superuser"]
        session.commit()
    finally:
        session.close()


@pytest.mark.browser
def test_comic_detail_metadata_modal_reads_and_writes_cbz(page, browser_server, tmp_path, monkeypatch):
    seed = browser_server["seed"]
    original_state = _prepare_editable_archive(browser_server, tmp_path)
    queued_tasks = []

    def fake_add_task(library_id, force=False):
        queued_tasks.append({"library_id": library_id, "force": force})
        return {"status": "queued", "message": "Scan queued"}

    monkeypatch.setattr("app.api.comics.scan_manager.add_task", fake_add_task)

    try:
        page.goto(f"{browser_server['base_url']}/comics/{seed['active_comic_id']}", wait_until="networkidle")
        page.get_by_role("heading", name=f"{seed['series_name']} #{seed['active_comic_number']}").wait_for()

        page.get_by_role("button", name="Edit metadata").click()
        page.get_by_role("heading", name="Edit Metadata").wait_for()
        page.wait_for_function(
            """
            () => document.querySelector("input[x-model='form.Series']")?.value === "Smoke Series"
            """
        )

        assert page.locator("input[x-model='form.Volume']").input_value() == "1"
        assert page.locator("input[x-model='form.Imprint']").input_value() == "Original Imprint"
        assert page.locator("input[x-model='form.Count']").input_value() == "5"
        assert page.locator("input[x-model='form.AlternateSeries']").input_value() == "Original Event"
        assert page.locator("input[x-model='form.AlternateNumber']").input_value() == "1"
        assert page.locator("input[x-model='form.Genre']").input_value() == "Original Genre"
        assert page.locator("select[x-model='form.Format']").input_value() == "Annual"
        assert page.locator("select[x-model='form.AgeRating']").input_value() == "Everyone"

        page.locator("input[x-model='form.Series']").fill("Browser Edited Series")
        page.locator("input[x-model='form.Volume']").fill("2")
        page.locator("input[x-model='form.Number']").fill("2A")
        page.locator("input[x-model='form.Title']").fill("Browser Edited Title")
        page.locator("input[x-model='form.Year']").fill("2025")
        page.locator("select[x-model='form.Format']").select_option("Special")
        page.locator("select[x-model='form.AgeRating']").select_option("Mature 17+")
        page.locator("input[x-model='form.Count']").fill("12")
        page.locator("input[x-model='form.Genre']").fill("Browser Genre, Sug")
        page.get_by_role("button", name="Browser Suggested Genre").click()
        assert page.locator("input[x-model='form.Genre']").input_value() == "Browser Genre, Browser Suggested Genre"
        page.locator("input[x-model='form.AlternateSeries']").fill("Browser Event")
        page.locator("input[x-model='form.AlternateNumber']").fill("4")
        page.locator("input[x-model='form.Writer']").fill("Browser Writer")
        page.locator("input[x-model='form.Penciller']").fill("Browser Penciller")
        page.locator("input[x-model='form.Publisher']").fill("Browser Publisher")
        page.locator("input[x-model='form.Imprint']").fill("Browser Imprint")
        page.locator("textarea[x-model='form.Summary']").fill("Updated from the browser metadata editor.")

        with page.expect_response(
            lambda response: (
                f"/api/comics/{seed['active_comic_id']}/metadata" in response.url
                and response.request.method == "PATCH"
            )
        ) as response_info:
            page.get_by_role("button", name="Save to File").click()

        assert response_info.value.status == 200
        page.wait_for_selector("text=File updated. Scan queued.")

        values = _comicinfo_values(original_state["archive_path"])
        assert values["Series"] == "Browser Edited Series"
        assert values["Volume"] == "2"
        assert values["Number"] == "2A"
        assert values["Title"] == "Browser Edited Title"
        assert values["Year"] == "2025"
        assert values["Format"] == "Special"
        assert values["AgeRating"] == "Mature 17+"
        assert values["Count"] == "12"
        assert values["Genre"] == "Browser Genre, Browser Suggested Genre"
        assert values["AlternateSeries"] == "Browser Event"
        assert values["AlternateNumber"] == "4"
        assert values["Writer"] == "Browser Writer"
        assert values["Penciller"] == "Browser Penciller"
        assert values["Publisher"] == "Browser Publisher"
        assert values["Imprint"] == "Browser Imprint"
        assert values["Summary"] == "Updated from the browser metadata editor."

        with zipfile.ZipFile(original_state["archive_path"], "r") as archive:
            assert archive.read("pages/001.jpg") == b"page-one"

        assert queued_tasks == [{"library_id": original_state["library_id"], "force": False}]
    finally:
        _restore_editable_archive_state(browser_server, original_state)


@pytest.mark.browser
def test_comic_detail_metadata_edit_button_hidden_when_archive_is_not_writable(
    page,
    browser_server,
    tmp_path,
    monkeypatch,
):
    seed = browser_server["seed"]
    original_state = _prepare_editable_archive(browser_server, tmp_path)

    monkeypatch.setattr("app.api.comics.metadata_service.can_write", lambda _path: False)

    try:
        page.goto(f"{browser_server['base_url']}/comics/{seed['active_comic_id']}", wait_until="networkidle")
        page.get_by_role("heading", name=f"{seed['series_name']} #{seed['active_comic_number']}").wait_for()

        assert page.get_by_role("button", name="Edit metadata").count() == 0
    finally:
        _restore_editable_archive_state(browser_server, original_state)
