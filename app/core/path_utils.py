import posixpath
from typing import List, Optional


def _segments(path: str) -> List[str]:
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return [part for part in normalized.split("/") if part not in ("", ".")]


def compute_relative_path(root_path: str, file_path: str) -> Optional[str]:
    """
    Compute file_path's location relative to root_path, tolerating separator/case
    normalization differences (e.g. mixed '/' vs '\\', or case-insensitive filesystems).
    Returns None if file_path is not under root_path.

    Comparisons are done per path segment rather than via os.sep/os.path.normpath/
    os.path.normcase, which reflect the *current* platform, not necessarily the one
    that produced the stored path string — this repo primarily ships via a Linux
    Docker image but is also run loose on Windows, and a path recorded under one
    needs to still resolve correctly when read back under the other. Segment-based
    comparison also sidesteps the fact that lowercasing a string for case-insensitive
    comparison can change its length for a handful of Unicode code points, which would
    otherwise corrupt a length-based slice of the original (case-preserved) string.

    The result always uses '/' as the separator, regardless of host OS, since a
    stored '\\'-separated path would be unusable (treated as a literal filename, not
    a subdirectory) if ever read back on the other platform.
    """
    root_segments = _segments(root_path)
    file_segments = _segments(file_path)

    if len(file_segments) < len(root_segments):
        return None

    prefix = file_segments[:len(root_segments)]
    if [part.lower() for part in prefix] != [part.lower() for part in root_segments]:
        return None

    return "/".join(file_segments[len(root_segments):])


def resolve_absolute_path(root_path: str, relative_path: str) -> str:
    """Reverse of compute_relative_path: rebuild an absolute path from a root and
    the relative path stored under it."""
    if not relative_path:
        return root_path

    separator = "\\" if root_path.count("\\") > root_path.count("/") else "/"
    normalized_relative = relative_path.replace("\\", "/").strip("/")
    relative_for_display = normalized_relative.replace("/", separator)
    if not root_path:
        return relative_for_display

    base = root_path.rstrip("/\\")

    if not base:
        return f"{separator}{relative_for_display}"

    return f"{base}{separator}{relative_for_display}"


def paths_overlap(first_path: str, second_path: str) -> bool:
    """
    True if one of the two paths is an ancestor of (or equal to) the other,
    compared per path segment rather than via os.path.commonpath/os.sep — see
    compute_relative_path for why that matters across platforms.
    """
    first_segments = _segments(first_path)
    second_segments = _segments(second_path)

    shorter, longer = (
        (first_segments, second_segments)
        if len(first_segments) <= len(second_segments)
        else (second_segments, first_segments)
    )

    prefix = longer[:len(shorter)]
    return [part.lower() for part in prefix] == [part.lower() for part in shorter]
