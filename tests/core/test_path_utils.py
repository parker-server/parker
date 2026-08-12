from app.core.path_utils import compute_relative_path, resolve_absolute_path


def test_compute_relative_path_basic_match():
    assert compute_relative_path("C:\\Comics\\DC", "C:\\Comics\\DC\\Batman\\Batman 001.cbz") == \
        "Batman/Batman 001.cbz"


def test_compute_relative_path_uses_forward_slash_even_with_backslash_input():
    result = compute_relative_path("C:\\Comics\\DC", "C:\\Comics\\DC\\OMAC Project\\OMAC 004.cbr")
    assert result == "OMAC Project/OMAC 004.cbr"
    assert "\\" not in result


def test_compute_relative_path_tolerates_mixed_separators():
    assert compute_relative_path("C:/Comics/DC", "C:\\Comics\\DC\\Batman\\Batman 001.cbz") == \
        "Batman/Batman 001.cbz"


def test_compute_relative_path_tolerates_case_difference():
    assert compute_relative_path("c:\\comics\\dc", "C:\\Comics\\DC\\Batman\\Batman 001.cbz") == \
        "Batman/Batman 001.cbz"


def test_compute_relative_path_file_at_root_returns_empty_string():
    assert compute_relative_path("C:\\Comics\\DC", "C:\\Comics\\DC") == ""


def test_compute_relative_path_returns_none_when_not_under_root():
    assert compute_relative_path("C:\\Comics\\DC", "C:\\Comics\\Marvel\\Spiderman.cbz") is None


def test_compute_relative_path_returns_none_for_sibling_with_shared_prefix():
    # "DC" should not match "DComics" — must respect the path separator boundary
    assert compute_relative_path("C:\\Comics\\DC", "C:\\Comics\\DComics\\file.cbz") is None


def test_compute_relative_path_windows_style_paths_regardless_of_host_os():
    # Backslash-separated paths must resolve correctly even when this code runs on
    # a host (e.g. Linux) whose native os.sep is '/' — matching must not depend on
    # which platform originally wrote the path string.
    assert compute_relative_path("C:\\Comics\\DC", "C:\\Comics\\DC\\Batman\\Batman 001.cbz") == \
        "Batman/Batman 001.cbz"


def test_compute_relative_path_root_at_filesystem_root():
    assert compute_relative_path("/", "/comics/dc/Batman 001.cbz") == "comics/dc/Batman 001.cbz"


def test_compute_relative_path_windows_drive_root():
    assert compute_relative_path("C:\\", "C:\\Comics\\DC\\Batman 001.cbz") == "Comics/DC/Batman 001.cbz"


def test_resolve_absolute_path_preserves_forward_slash_root_style():
    result = resolve_absolute_path(
        "D:/_ComicTests/DC",
        "Pulp Fantastic/Pulp Fantastic #001 {2000}.cbr",
    )

    assert result == "D:/_ComicTests/DC/Pulp Fantastic/Pulp Fantastic #001 {2000}.cbr"


def test_resolve_absolute_path_preserves_backslash_root_style():
    result = resolve_absolute_path(
        "D:\\_ComicTests\\DC",
        "Pulp Fantastic/Pulp Fantastic #001 {2000}.cbr",
    )

    assert result == "D:\\_ComicTests\\DC\\Pulp Fantastic\\Pulp Fantastic #001 {2000}.cbr"


def test_resolve_absolute_path_handles_filesystem_root():
    assert resolve_absolute_path("/", "comics/dc/Batman 001.cbz") == "/comics/dc/Batman 001.cbz"
