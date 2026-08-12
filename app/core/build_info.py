import os
import subprocess
from functools import lru_cache
from pathlib import Path


BUILD_COMMIT_ENV_KEYS = (
    "PARKER_BUILD_COMMIT",
    "PARKER_GIT_COMMIT",
    "GIT_COMMIT",
    "SOURCE_COMMIT",
    "COMMIT_SHA",
)


@lru_cache(maxsize=1)
def get_build_commit_hash() -> str | None:
    for key in BUILD_COMMIT_ENV_KEYS:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return result.stdout.strip() or None
