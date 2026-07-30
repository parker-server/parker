import asyncio
import hashlib
import ipaddress
import json
import logging
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.cbl_source import CBLSource
from app.models.reading_list import ReadingList
from app.services.cbl_matching import match_cbl_entries
from app.services.cbl_parser import CBLParseError, parse_cbl
from app.services.reading_list import ReadingListService

logger = logging.getLogger(__name__)

MAX_CBL_SIZE_BYTES = 5 * 1024 * 1024  # 5MB -- CBL files are plain XML text lists
ALLOWED_EXTENSIONS = (".cbl", ".xml")
URL_FETCH_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


def _pin_url_to_address(url: str, address: str) -> str:
    """
    Rewrite `url`'s host to `address` (a pre-resolved, already-validated IP),
    preserving scheme/port/path/query. Used together with the `sni_hostname`
    request extension so the actual TCP connection targets a known-safe
    address instead of letting the HTTP client re-resolve the hostname
    independently (which would reopen a DNS-rebinding window between the
    safety check and the real connection).
    """
    parsed = urlparse(url)
    ip = ipaddress.ip_address(address)
    host = f"[{address}]" if ip.version == 6 else address
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc))


class CBLSourceError(Exception):
    """Raised for invalid/unsafe CBL content or import requests (maps to a 4xx at the API layer)."""


def _looks_like_cbl_xml(content: bytes) -> bool:
    head = content[:512].lstrip()
    return head.startswith(b"<?xml") or b"<ReadingList" in content[:2048]


def _fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class CBLSourceService:
    """Manages Parker's own copy of imported .cbl files (storage/cbl), independent
    of whatever comic library roots exist -- see docs/cbl-reading-list-support-scope.md."""

    def __init__(self, db: Session):
        self.db = db
        self.logger = logging.getLogger(__name__)

    def _validate_content(self, content: bytes, filename: str) -> None:
        if not content:
            raise CBLSourceError("File is empty")
        if len(content) > MAX_CBL_SIZE_BYTES:
            raise CBLSourceError(f"File too large. Maximum size is {MAX_CBL_SIZE_BYTES // (1024 * 1024)}MB.")
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            raise CBLSourceError("File must have a .cbl or .xml extension")
        if not _looks_like_cbl_xml(content):
            raise CBLSourceError("File does not look like a CBL/XML reading list")

    def _write_stored_file(self, content: bytes, fingerprint: str) -> Path:
        settings.cbl_dir.mkdir(parents=True, exist_ok=True)
        stored_path = settings.cbl_dir / f"{fingerprint}.cbl"
        if not stored_path.exists():
            stored_path.write_bytes(content)
        return stored_path

    def import_upload(
        self,
        content: bytes,
        original_filename: str,
        origin: str = "upload",
        catalog_provider: str | None = None,
        catalog_path: str | None = None,
    ) -> CBLSource:
        self._validate_content(content, original_filename)
        fingerprint = _fingerprint(content)

        existing = self.db.query(CBLSource).filter(CBLSource.fingerprint == fingerprint).first()
        if existing:
            raise CBLSourceError(f"This exact CBL file is already imported (source id {existing.id})")

        stored_path = self._write_stored_file(content, fingerprint)

        source = CBLSource(
            display_name=Path(original_filename).stem,
            stored_path=str(stored_path),
            original_filename=original_filename,
            origin=origin,
            catalog_provider=catalog_provider,
            catalog_path=catalog_path,
            fingerprint=fingerprint,
            last_refresh_status="never",
        )
        self.db.add(source)
        self.db.flush()
        return source

    def _resolve_safe_addresses(self, host: str) -> list[str]:
        """
        Resolve `host` and validate every returned address is public. Returns
        the resolved addresses (so the caller can pin the actual connection to
        one of them) rather than just a pass/fail signal -- see _safe_fetch.
        """
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise CBLSourceError(f"Could not resolve host: {host}") from exc

        addresses = []
        for info in infos:
            ip_str = info[4][0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise CBLSourceError(f"Refusing to fetch from a non-public address ({ip_str})")
            addresses.append(ip_str)

        if not addresses:
            raise CBLSourceError(f"Could not resolve host: {host}")

        return addresses

    async def _safe_fetch(self, url: str) -> bytes:
        """
        Fetch `url`, pinning the actual connection to a pre-validated address
        instead of letting httpx resolve the hostname a second time (which
        would reopen the DNS-rebinding window between the safety check and
        the real connection -- see _resolve_safe_addresses/_pin_url_to_address).
        TLS SNI and certificate validation still target the real hostname via
        the `sni_hostname` extension, so this doesn't affect cert validation.
        """
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise CBLSourceError("Only https:// URLs are supported")
        if not parsed.hostname:
            raise CBLSourceError("URL is missing a hostname")

        hostname = parsed.hostname
        addresses = await asyncio.to_thread(self._resolve_safe_addresses, hostname)
        pinned_url = _pin_url_to_address(url, addresses[0])

        content = bytearray()
        try:
            async with httpx.AsyncClient(timeout=URL_FETCH_TIMEOUT, follow_redirects=False) as client:
                async with client.stream(
                    "GET", pinned_url,
                    headers={"Host": hostname},
                    extensions={"sni_hostname": hostname},
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_CBL_SIZE_BYTES:
                            raise CBLSourceError(
                                f"Remote file exceeds maximum size of {MAX_CBL_SIZE_BYTES // (1024 * 1024)}MB"
                            )
        except httpx.HTTPError as exc:
            raise CBLSourceError(f"Failed to download CBL from URL: {exc}") from exc

        return bytes(content)

    async def import_url(self, url: str) -> CBLSource:
        content_bytes = await self._safe_fetch(url)

        parsed = urlparse(url)
        filename = Path(parsed.path).name or "import.cbl"
        self._validate_content(content_bytes, filename if "." in filename else f"{filename}.cbl")

        fingerprint = _fingerprint(content_bytes)
        existing = self.db.query(CBLSource).filter(CBLSource.fingerprint == fingerprint).first()
        if existing:
            raise CBLSourceError(f"This exact CBL file is already imported (source id {existing.id})")

        stored_path = self._write_stored_file(content_bytes, fingerprint)

        source = CBLSource(
            display_name=Path(filename).stem,
            stored_path=str(stored_path),
            original_filename=filename,
            origin="url",
            source_url=url,
            fingerprint=fingerprint,
            last_refresh_status="never",
        )
        self.db.add(source)
        self.db.flush()
        return source

    def apply_refreshed_content(self, source: CBLSource, content_bytes: bytes) -> None:
        """
        Update `source`'s stored file/fingerprint/status after a successful
        refresh fetch. Shared by both the direct-URL refresh path (below) and
        CBLCatalogService's catalog-path refresh, so "what do we do with newly
        fetched bytes" stays in one place regardless of how they were fetched.

        Parses the new content BEFORE touching stored_path/fingerprint/status --
        "looks like CBL/XML" (a cheap prefix sniff, in _validate_content) is not
        the same as "actually parses". A refresh that replaces the last good
        file and reports last_refresh_status="ok" for content that then fails
        to parse would be worse than just failing the refresh outright.
        Only touches the file on disk if the content actually changed.
        """
        self._validate_content(content_bytes, source.original_filename or "refresh.cbl")

        filename_stem = Path(source.original_filename or "refresh").stem
        try:
            parse_cbl(content_bytes, filename_stem=filename_stem)
        except CBLParseError as exc:
            raise CBLSourceError(f"Refreshed content failed to parse: {exc}") from exc

        new_fingerprint = _fingerprint(content_bytes)
        if new_fingerprint != source.fingerprint:
            old_path = Path(source.stored_path)
            new_path = self._write_stored_file(content_bytes, new_fingerprint)
            source.stored_path = str(new_path)
            source.fingerprint = new_fingerprint
            if old_path.exists() and old_path != new_path:
                try:
                    old_path.unlink()
                except OSError:
                    pass

        source.last_refresh_status = "ok"
        source.last_refreshed_at = datetime.now(timezone.utc)

    def mark_refresh_failed(self, source: CBLSource, exc: Exception) -> None:
        # Remote unavailability must not delete the last successfully imported
        # CBL-derived list -- only record that the refresh failed.
        self.logger.warning("CBL refresh failed for source %s: %s", source.id, exc)
        source.last_refresh_status = "failed"
        source.last_refreshed_at = datetime.now(timezone.utc)

    async def refresh(self, source_id: int) -> CBLSource:
        source = self.db.get(CBLSource, source_id)
        if not source:
            raise ValueError(f"CBL source {source_id} not found")

        if not source.source_url:
            raise CBLSourceError("This CBL source has no URL to refresh from")

        try:
            content_bytes = await self._safe_fetch(source.source_url)
            self.apply_refreshed_content(source, content_bytes)
        except (CBLSourceError, httpx.HTTPError) as exc:
            self.mark_refresh_failed(source, exc)

        self.db.flush()
        return source

    def rebuild(self, source_id: int) -> CBLSource:
        """
        Parse the stored file, match its entries against the current DB, and
        sync the CBL-owned ReadingList. Safe to call repeatedly (e.g. after
        every library scan) -- matching only ever touches the ReadingList tied
        to this source (see ReadingListService.sync_cbl_list).
        """
        source = self.db.get(CBLSource, source_id)
        if not source:
            raise ValueError(f"CBL source {source_id} not found")

        content = Path(source.stored_path).read_bytes()
        filename_stem = Path(source.original_filename or source.stored_path).stem

        try:
            parsed = parse_cbl(content, filename_stem=filename_stem)
        except CBLParseError as exc:
            self.logger.warning("CBL parse failed for source %s: %s", source_id, exc)
            source.last_match_summary = json.dumps({"error": str(exc)})
            self.db.flush()
            return source

        match_result = match_cbl_entries(self.db, parsed.entries)

        reading_list_service = ReadingListService(self.db)
        reading_list_service.sync_cbl_list(
            source,
            parsed.name,
            parsed.description,
            match_result.ordered_comic_ids,
        )

        source.entry_count = len(parsed.entries)
        source.last_match_summary = match_result.summary_json()
        self.db.flush()
        return source

    def delete(self, source_id: int) -> None:
        source = self.db.get(CBLSource, source_id)
        if not source:
            raise ValueError(f"CBL source {source_id} not found")

        reading_list = self.db.query(ReadingList).filter(ReadingList.source_cbl_id == source.id).first()
        if reading_list:
            self.db.delete(reading_list)

        stored_path = Path(source.stored_path)
        if stored_path.exists():
            try:
                stored_path.unlink()
            except OSError as exc:
                self.logger.warning("Failed to delete stored CBL file %s: %s", stored_path, exc)

        self.db.delete(source)
        self.db.commit()
