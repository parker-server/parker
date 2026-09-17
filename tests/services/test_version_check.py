from app.services.version_check import build_version_check_status, parse_version


def test_parse_version_accepts_plain_and_prefixed_semver():
    assert parse_version("0.1.34") == (0, 1, 34)
    assert parse_version("v0.1.35") == (0, 1, 35)
    assert parse_version("release-0.1.35") is None


def test_build_version_check_status_reports_newer_tag():
    status = build_version_check_status(
        "0.1.34",
        [
            {"name": "v0.1.33"},
            {"name": "not-a-version"},
            {"name": "v0.1.35"},
        ],
    )

    assert status.status == "update_available"
    assert status.update_available is True
    assert status.latest_tag == "v0.1.35"
    assert status.latest_version == "0.1.35"
    assert status.latest_url == "https://github.com/parker-server/parker/tree/v0.1.35"


def test_build_version_check_status_reports_current_when_latest_matches():
    status = build_version_check_status(
        "0.1.35",
        [
            {"name": "v0.1.34"},
            {"name": "v0.1.35"},
        ],
    )

    assert status.status == "current"
    assert status.update_available is False
    assert status.latest_tag == "v0.1.35"


def test_build_version_check_status_handles_missing_semver_tags():
    status = build_version_check_status("0.1.35", [{"name": "latest"}])

    assert status.status == "unavailable"
    assert status.update_available is False
    assert status.latest_tag is None
    assert status.error == "No semantic version tags were found."
