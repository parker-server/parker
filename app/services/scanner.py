from pathlib import Path
from typing import Optional
import hashlib
import os
import time
import logging
import multiprocessing
from queue import Empty
from multiprocessing import Queue
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.settings_loader import get_cached_setting
from app.core.path_utils import compute_relative_path
from app.models.library import Library
from app.models.library_root import LibraryRoot
from app.models.comic import Comic
from app.models.comic import Volume
from app.models.series import Series
from app.models.cbl_source import CBLSource

from app.services.workers.metadata_worker import metadata_worker
from app.services.workers.metadata_writer import metadata_writer
from app.services.sidecar_service import SidecarService
from app.services.reading_list import ReadingListService
from app.services.collection import CollectionService
from app.services.cbl_source_service import CBLSourceService, CBLSourceError


class LibraryScanner:
    """Scans library directories and imports comics with batch processing"""

    def __init__(self, library: Library, db: Session):
        self.library = library
        self.db = db
        self.supported_extensions = ['.cbz', '.cbr']
        self.logger = logging.getLogger(__name__)

        self.reconciled_folders = set()
        self.reconciled_volumes = set()
        self.reconciled_series = set()

        self.reading_list_service = ReadingListService(db)
        self.collection_service = CollectionService(db)
        self.cbl_source_service = CBLSourceService(db)

    def scan_parallel(self, force: bool = False, worker_limit: int = 0) -> dict:
        """
        Parallel metadata extraction + single-writer DB updates.
        """
        library_roots = (
            self.db.query(LibraryRoot)
            .filter(LibraryRoot.library_id == self.library.id, LibraryRoot.is_active == True)
            .order_by(LibraryRoot.id)
            .all()
        )
        if not library_roots:
            raise FileNotFoundError(f"Library '{self.library.name}' has no active roots configured")

        root_paths = [(library_root, Path(library_root.path)) for library_root in library_roots]

        for library_root, library_path in root_paths:
            if not library_path.exists():
                raise FileNotFoundError(f"Library path does not exist: {library_root.path}")

        self.reconciled_folders.clear()
        self.reconciled_volumes.clear()
        self.reconciled_series.clear()

        start_time = time.time()

        # 0. Prefetch existing comics for roots this scan can actually see.
        self.logger.debug("Pre-fetching existing file list for parallel scan...")
        active_root_ids = [library_root.id for library_root in library_roots]
        db_comics = (
            self.db.query(Comic)
            .join(Volume)
            .join(Series)
            .filter(
                Series.library_id == self.library.id,
                Comic.library_root_id.in_(active_root_ids),
            )
            .all()
        )

        existing_by_key = {(c.library_root_id, c.relative_path): c for c in db_comics}

        # --- Phase 1: Build task list with skip/import/update logic ---
        tasks = []
        scanned_keys = set()
        skipped = 0
        discovered_cbl_files: list[tuple] = []

        for library_root, library_path in root_paths:
            for file_path in library_path.rglob("*"):
                if file_path.suffix.lower() == ".cbl":
                    discovered_cbl_files.append((library_root, file_path))
                    continue

                if file_path.suffix.lower() not in self.supported_extensions:
                    continue

                file_path_str = str(file_path)
                # Always resolves: file_path_str is necessarily a child of this
                # library_path, since rglob() only yields files physically nested under it.
                relative_path = compute_relative_path(library_root.path, file_path_str)
                key = (library_root.id, relative_path)
                existing = existing_by_key.get(key)

                # --- CONTAINER RECONCILIATION ---
                # This runs for EVERY file, but the logic inside ensures it only
                # performs the disk-check once per folder.
                self._reconcile_sidecars(file_path, existing, library_root.path)

                scanned_keys.add(key)

                file_mtime = os.path.getmtime(file_path)

                if existing:
                    # unchanged file -> skip unless force=True
                    if not force and existing.file_modified_at and existing.file_modified_at >= file_mtime:
                        skipped += 1
                        continue

                    # changed file -> update
                    tasks.append({
                        "file_path": file_path_str,
                        "library_root_id": library_root.id,
                        "library_root_path": library_root.path,
                    })
                else:
                    # new file -> import
                    tasks.append({
                        "file_path": file_path_str,
                        "library_root_id": library_root.id,
                        "library_root_path": library_root.path,
                    })

        # --- Optional CBL discovery: import any .cbl files found under active
        # roots into Parker-managed storage. This is an import source, not
        # canonical storage -- the managed copy becomes the thing Parker
        # re-parses/re-matches from here on, so already-known fingerprints are
        # skipped silently rather than re-imported every scan.
        for library_root, cbl_path in discovered_cbl_files:
            try:
                content = cbl_path.read_bytes()
            except OSError as exc:
                self.logger.warning("Could not read discovered CBL file %s: %s", cbl_path, exc)
                continue

            fingerprint = hashlib.sha256(content).hexdigest()
            if self.db.query(CBLSource).filter(CBLSource.fingerprint == fingerprint).first():
                continue

            relative_path = compute_relative_path(library_root.path, str(cbl_path)) or cbl_path.name
            try:
                self.cbl_source_service.import_upload(content, relative_path, origin="library_import")
                self.logger.info(f"Discovered and imported CBL file: {relative_path}")
            except CBLSourceError as exc:
                self.logger.warning("Skipping invalid discovered CBL file %s: %s", cbl_path, exc)

        # Persist any sidecar reconciliation updates found during discovery.
        self.db.commit()

        summary = {"imported": 0, "updated": 0, "errors": 0, "skipped": 0}
        parse_reading_lists = bool(getattr(self.library, "parse_reading_lists", True))
        parse_collections = bool(getattr(self.library, "parse_collections", True))
        parse_story_arcs = bool(getattr(self.library, "parse_story_arcs", True))

        if tasks:
            result_queue = Queue()
            stats_queue = Queue()

            # --- Start writer ---
            writer_proc = multiprocessing.Process(
                target=metadata_writer,
                args=(result_queue, stats_queue, self.library.id),
                kwargs={
                    "batch_size": 50,
                    "parse_reading_lists": parse_reading_lists,
                    "parse_collections": parse_collections,
                    "parse_story_arcs": parse_story_arcs,
                }
            )
            writer_proc.start()

            summary_timeout = int(
                get_cached_setting("system.parallel_metadata_writer_summary_timeout_seconds", 180)
            )
            summary_timeout = max(10, summary_timeout)

            join_timeout = int(
                get_cached_setting("system.parallel_metadata_writer_join_timeout_seconds", 30)
            )
            join_timeout = max(1, join_timeout)

            sentinel_sent = False
            try:
                # Determine Worker Count
                if worker_limit > 0:
                    workers = worker_limit
                    if worker_limit == 1:
                        self.logger.info("Using exactly 1 worker for metadata extraction (SERIAL MODE)")
                    else:
                        self.logger.info(f"Using exactly {worker_limit} worker(s) for metadata extraction")
                else:
                    requested_workers = int(get_cached_setting("system.parallel_metadata_workers", 0))
                    requested_label = "(Auto)" if requested_workers <= 0 else requested_workers
                    self.logger.info(
                        f"Requested {requested_label} worker(s) for parallel metadata extraction"
                    )

                    if requested_workers <= 0:
                        # AUTO MODE:
                        # Use 50% of cores, with a minimum of 1.
                        # This prevents system starvation when multiple web workers are active.
                        total_cores = multiprocessing.cpu_count() or 1
                        workers = max(1, total_cores // 2)
                    else:
                        max_cores = multiprocessing.cpu_count() or 1
                        workers = min(requested_workers, max_cores)

                    self.logger.info(f"Using {workers} worker(s) for parallel metadata extraction")

                # Start Workers (only for needed files) (CPU bound)
                self.logger.debug(f"Scanning {len(tasks)} comic(s)")
                with multiprocessing.Pool(processes=workers) as pool:
                    for payload in pool.imap_unordered(metadata_worker, tasks):
                        result_queue.put(payload)

                # Signal writer to finish
                result_queue.put(None)
                sentinel_sent = True

                # Wait for summary, but do not block forever.
                try:
                    summary = stats_queue.get(timeout=summary_timeout)
                except Empty as exc:
                    raise TimeoutError(
                        f"Timed out after {summary_timeout}s waiting for metadata writer summary"
                    ) from exc
            finally:
                if not sentinel_sent:
                    try:
                        result_queue.put(None)
                    except Exception:
                        pass

                if writer_proc.is_alive():
                    writer_proc.join(timeout=join_timeout)

                if writer_proc.is_alive():
                    self.logger.error("Metadata writer did not exit cleanly; terminating process.")
                    writer_proc.terminate()
                    writer_proc.join(timeout=5)

        # --- Phase 3: Cleanup missing files ---
        deleted = self._cleanup_missing_files(scanned_keys, existing_by_key)

        # Cleanup empty containers
        self.reading_list_service.cleanup_empty_lists()
        self.collection_service.cleanup_empty_collections()

        # Rebuild/rematch every managed CBL source -- not just ones tied to this
        # library, since CBL events can span comics from multiple libraries and
        # this scan's newly-imported comics may resolve previously-unmatched
        # entries from any of them. One bad source shouldn't sink the scan.
        for cbl_source in self.db.query(CBLSource).all():
            try:
                self.cbl_source_service.rebuild(cbl_source.id)
            except Exception as exc:
                self.logger.error(f"Failed to rebuild CBL source {cbl_source.id}: {exc}")

        # Update library scan time
        scanned_at = datetime.now(timezone.utc)
        self.library.last_scanned = scanned_at
        for library_root in library_roots:
            library_root.last_scanned_at = scanned_at
            library_root.last_scan_error = None
        self.db.commit()

        elapsed = round(time.time() - start_time, 2)

        return {
            "library": self.library.name,
            "skipped": skipped + summary.get("skipped", 0),
            "imported": summary.get("imported", 0),
            "updated": summary.get("updated", 0),
            "deleted": deleted,
            "errors": summary.get("errors", 0),
            "elapsed": elapsed,
        }

    def _cleanup_missing_files(self, scanned_keys: set, existing_by_key: dict) -> int:
        """Remove comics from DB whose files no longer exist"""
        deleted = 0

        for key, comic in existing_by_key.items():
            if key not in scanned_keys:
                self.logger.info(f"Removing deleted comic: {comic.filename}")
                self.db.delete(comic)
                deleted += 1

        if deleted > 0:
            self.db.commit()

        return deleted

    def _resolve_sidecar_from_parents(
        self,
        start_path: Path,
        entity_type: str,
        boundary_path: Path,
    ) -> Optional[str]:
        """
        Resolve sidecar by checking this folder first, then walking upward
        until the library boundary.
        """
        boundary_key = os.path.normcase(os.path.normpath(str(boundary_path)))
        current = start_path

        while True:
            summary = SidecarService.get_summary_from_disk(current, entity_type)
            if summary is not None:
                return summary

            current_key = os.path.normcase(os.path.normpath(str(current)))
            if current_key == boundary_key:
                return None

            parent = current.parent
            if parent == current:
                return None

            current = parent

    def _reconcile_sidecars(self, file_path: Path, existing_comic: Optional[Comic], library_root_path: str):
        """
        Sync folder-level sidecars with Series/Volume models.
        Works even if individual comics are skipped.
        """
        folder_path = file_path.parent
        folder_str = str(folder_path)
        lib_path = Path(library_root_path)

        # If the comic is in the root, there is no 'Series' or 'Volume' folder to reconcile
        if folder_path == lib_path:
            self.logger.debug(
                f"Skipping sidecar reconciliation for {folder_str} (in root folder)"
            )
            return

        if folder_str in self.reconciled_folders:
            return

        if not existing_comic:
            self.logger.debug(
                "Skipping sidecar reconciliation for unknown comic path; "
                "import path will handle first-time sync"
            )
            return

        # 1. Update Volume (Clear if missing)
        vol = existing_comic.volume
        series = vol.series if vol else None

        if vol and vol.id not in self.reconciled_volumes:
            disk_vol_summary = self._resolve_sidecar_from_parents(folder_path, "volume", lib_path)
            if vol.summary_override != disk_vol_summary:
                vol.summary_override = disk_vol_summary
                self.logger.info(
                    f"Sidecar: Updated Volume {vol.volume_number} summary for Series '{vol.series.name}'."
                )
            self.reconciled_volumes.add(vol.id)

        if series and series.id not in self.reconciled_series:
            disk_series_summary = self._resolve_sidecar_from_parents(folder_path, "series", lib_path)
            if series.summary_override != disk_series_summary:
                series.summary_override = disk_series_summary
                self.logger.info(f"Sidecar: Updated Series '{series.name}' summary.")
            self.reconciled_series.add(series.id)

        self.reconciled_folders.add(folder_str)
