from pathlib import Path
from typing import Optional
import os
import time
import logging
import multiprocessing
from multiprocessing import Queue
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.settings_loader import get_cached_setting
from app.models.library import Library
from app.models.comic import Comic
from app.models.comic import Volume
from app.models.series import Series

from app.services.workers.metadata_worker import metadata_worker
from app.services.workers.metadata_writer import metadata_writer
from app.services.sidecar_service import SidecarService
from app.services.reading_list import ReadingListService
from app.services.collection import CollectionService


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

    def scan_parallel(self, force: bool = False, worker_limit: int = 0) -> dict:
        """
        Parallel metadata extraction + single-writer DB updates.
        """
        library_path = Path(self.library.path)

        if not library_path.exists():
            raise FileNotFoundError(f"Library path does not exist: {self.library.path}")

        self.reconciled_folders.clear()
        self.reconciled_volumes.clear()
        self.reconciled_series.clear()

        start_time = time.time()

        self.logger.debug("Pre-fetching existing file list for parallel scan...")
        db_comics = (
            self.db.query(Comic)
            .join(Volume)
            .join(Series)
            .filter(Series.library_id == self.library.id)
            .all()
        )

        existing_map = {c.file_path: c for c in db_comics}

        tasks = []
        scanned_paths_on_disk = set()
        skipped = 0

        for file_path in library_path.rglob("*"):
            if file_path.suffix.lower() not in self.supported_extensions:
                continue

            self._reconcile_sidecars(file_path, existing_map)

            file_path_str = str(file_path)
            scanned_paths_on_disk.add(file_path_str)

            file_mtime = os.path.getmtime(file_path)
            existing = existing_map.get(file_path_str)

            if existing:
                if not force and existing.file_modified_at and existing.file_modified_at >= file_mtime:
                    skipped += 1
                    continue

                tasks.append(file_path_str)
            else:
                tasks.append(file_path_str)

        # Persist any sidecar reconciliation updates found during discovery.
        self.db.commit()

        summary = {"imported": 0, "updated": 0, "errors": 0, "skipped": 0}

        if tasks:
            result_queue = Queue()
            stats_queue = Queue()

            writer_proc = multiprocessing.Process(
                target=metadata_writer,
                args=(result_queue, stats_queue, self.library.id),
                kwargs={"batch_size": 50}
            )
            writer_proc.start()

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
                    total_cores = multiprocessing.cpu_count() or 1
                    workers = max(1, total_cores // 2)
                else:
                    max_cores = multiprocessing.cpu_count() or 1
                    workers = min(requested_workers, max_cores)

                self.logger.info(f"Using {workers} worker(s) for parallel metadata extraction")

            self.logger.debug(f"Scanning {len(tasks)} comic(s)")
            with multiprocessing.Pool(processes=workers) as pool:
                for payload in pool.imap_unordered(metadata_worker, tasks):
                    result_queue.put(payload)

            result_queue.put(None)
            summary = stats_queue.get()
            writer_proc.join()

        deleted = self._cleanup_missing_files(scanned_paths_on_disk, existing_map)

        self.reading_list_service.cleanup_empty_lists()
        self.collection_service.cleanup_empty_collections()

        self.library.last_scanned = datetime.now(timezone.utc)
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

    def _cleanup_missing_files(self, scanned_paths_on_disk: set, existing_map: dict) -> int:
        """Remove comics from DB whose files no longer exist"""
        deleted = 0

        for file_path, comic in existing_map.items():
            if file_path not in scanned_paths_on_disk:
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

    def _reconcile_sidecars(self, file_path: Path, existing_map: dict):
        """
        Sync folder-level sidecars with Series/Volume models.
        Works even if individual comics are skipped.
        """
        folder_path = file_path.parent
        folder_str = str(folder_path)
        lib_path = Path(self.library.path)

        if folder_path == lib_path:
            self.logger.debug(
                f"Skipping sidecar reconciliation for {folder_str} (in root folder)"
            )
            return

        if folder_str in self.reconciled_folders:
            return

        existing_comic = existing_map.get(str(file_path))
        if not existing_comic:
            self.logger.debug(
                "Skipping sidecar reconciliation for unknown comic path; "
                "import path will handle first-time sync"
            )
            return

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
