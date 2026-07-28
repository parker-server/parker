import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.models.cbl_source import CBLSource
from app.services.cbl_parser import parse_cbl
from app.services.cbl_source_service import MAX_CBL_SIZE_BYTES, CBLSourceError, CBLSourceService

CATALOG_PROVIDER = "dieseltech"
GITHUB_API_BASE = "https://api.github.com/repos/DieselTech/CBL-ReadingLists"
GITHUB_RAW_HOST = "raw.githubusercontent.com"
FETCH_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
CACHE_TTL_SECONDS = 15 * 60
_REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    # GitHub's REST API 403s unauthenticated requests that omit a User-Agent.
    "User-Agent": "Parker-CBL-Catalog",
}


class CBLCatalogError(Exception):
    """Base class for catalog browsing failures."""


class CBLCatalogNotFoundError(CBLCatalogError):
    """The requested path does not exist in the catalog repository."""


class CBLCatalogUpstreamError(CBLCatalogError):
    """GitHub is unreachable, rate-limited, or returned an unexpected response."""


class CBLCatalogService:
    """Browses the DieselTech/CBL-ReadingLists GitHub repo and imports files from
    it into Parker-managed CBL storage. Single built-in provider for MVP, no
    GitHub token required -- see docs/cbl-reading-list-support-scope.md."""

    _cache: dict[str, tuple[float, dict]] = {}

    def __init__(self, db: Session):
        self.db = db
        self.source_service = CBLSourceService(db)

    async def _get_contents(self, path: str):
        url = f"{GITHUB_API_BASE}/contents/{path}".rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                response = await client.get(url, headers=_REQUEST_HEADERS)
        except httpx.HTTPError as exc:
            raise CBLCatalogUpstreamError(f"Failed to reach GitHub: {exc}") from exc

        if response.status_code == 404:
            raise CBLCatalogNotFoundError(f"Path not found in catalog repository: {path or '/'}")
        if response.status_code in (403, 429):
            raise CBLCatalogUpstreamError("GitHub API rate limit exceeded. Try again later.")
        if response.status_code != 200:
            raise CBLCatalogUpstreamError(f"GitHub API returned unexpected status {response.status_code}")

        return response.json()

    async def browse(self, path: str = "", force_refresh: bool = False) -> dict:
        cache_key = path.strip("/")
        now = time.monotonic()

        if not force_refresh and cache_key in self._cache:
            cached_at, payload = self._cache[cache_key]
            if now - cached_at < CACHE_TTL_SECONDS:
                return payload

        data = await self._get_contents(cache_key)
        if not isinstance(data, list):
            raise CBLCatalogNotFoundError(f"Path is not a folder: {path}")

        entries = []
        for item in data:
            if item.get("type") == "dir":
                entries.append({"name": item["name"], "path": item["path"], "type": "dir"})
            elif item.get("type") == "file" and item.get("name", "").lower().endswith(".cbl"):
                entries.append({"name": item["name"], "path": item["path"], "type": "file"})

        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))

        payload = {"path": cache_key, "entries": entries}
        self._cache[cache_key] = (now, payload)
        return payload

    async def _fetch_raw_bytes(self, download_url: str) -> bytes:
        host = urlparse(download_url).hostname
        if host != GITHUB_RAW_HOST:
            raise CBLCatalogError(f"Refusing to fetch from unexpected host: {host}")

        content = bytearray()
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False) as client:
                async with client.stream("GET", download_url, headers=_REQUEST_HEADERS) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_CBL_SIZE_BYTES:
                            raise CBLCatalogError(
                                f"Catalog file exceeds maximum size of {MAX_CBL_SIZE_BYTES // (1024 * 1024)}MB"
                            )
        except httpx.HTTPError as exc:
            raise CBLCatalogUpstreamError(f"Failed to download catalog file: {exc}") from exc

        return bytes(content)

    async def _get_file_bytes(self, path: str) -> tuple[bytes, str]:
        meta = await self._get_contents(path)
        if isinstance(meta, list) or meta.get("type") != "file":
            raise CBLCatalogNotFoundError(f"Path is not a file: {path}")

        content = await self._fetch_raw_bytes(meta["download_url"])
        return content, meta["name"]

    async def preview(self, path: str) -> dict:
        content, name = await self._get_file_bytes(path)
        parsed = parse_cbl(content, filename_stem=Path(name).stem)
        return {"name": parsed.name, "entry_count": len(parsed.entries), "warnings": parsed.warnings}

    async def import_file(self, path: str) -> CBLSource:
        content, name = await self._get_file_bytes(path)
        return self.source_service.import_upload(
            content, name, origin="catalog", catalog_provider=CATALOG_PROVIDER, catalog_path=path
        )

    async def refresh_source(self, source_id: int) -> CBLSource:
        """
        Refresh a catalog-origin CBLSource by re-resolving its file from GitHub
        via `catalog_path` rather than a stored URL. GitHub's `download_url`
        can shift if the repo restructures around a path (folder rename, file
        move); re-resolving by path through the same Contents API lookup used
        at import/preview time is more durable than caching a raw URL.
        """
        source = self.db.get(CBLSource, source_id)
        if not source:
            raise ValueError(f"CBL source {source_id} not found")

        if source.origin != "catalog" or not source.catalog_path:
            raise CBLSourceError("This CBL source has no catalog path to refresh from")

        try:
            content, _ = await self._get_file_bytes(source.catalog_path)
            self.source_service.apply_refreshed_content(source, content)
        except (CBLCatalogError, CBLSourceError) as exc:
            self.source_service.mark_refresh_failed(source, exc)

        self.db.flush()
        return source
