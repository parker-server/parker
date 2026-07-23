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
