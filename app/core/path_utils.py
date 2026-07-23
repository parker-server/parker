import os
from typing import Optional


def compute_relative_path(root_path: str, file_path: str) -> Optional[str]:
    """
    Compute file_path's location relative to root_path, tolerating separator/case
    normalization differences (e.g. mixed '/' vs '\\', or case-insensitive filesystems).
    Returns None if file_path is not under root_path.

    The result always uses '/' as the separator, regardless of host OS — this repo
    primarily ships via a Linux Docker image but is also run loose on Windows, and a
    stored '\\'-separated path would be unusable (treated as a literal filename, not
    a subdirectory) if ever read back on the other platform.
    """
    match_root = os.path.normcase(os.path.normpath(root_path.strip()))
    match_file = os.path.normcase(os.path.normpath(file_path.strip()))
    case_root = os.path.normpath(root_path.strip())
    case_file = os.path.normpath(file_path.strip())

    if match_file == match_root:
        return ""
    if match_file.startswith(match_root + os.sep):
        return case_file[len(case_root):].lstrip("/\\").replace("\\", "/")
    return None
