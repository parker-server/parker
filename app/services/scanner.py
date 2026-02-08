from pathlib import Path
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
from app.models.series import Series
from app.models.comic import Volume

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
        self.reading_list_service = ReadingListService(db)
        self.collection_service = CollectionService(db)


    def scan_parallel(self, force: bool = False, worker_limit: int = 0) -> dict:
        """
        Parallel metadata extraction + single-writer DB updates.
        """
        library_path = Path(self.library.path)

        if not library_path.exists():
            raise FileNotFoundError(f"Library path does not exist: {self.library.path}")

        start_time = time.time()

        # 0. Prefetch existing comics (same as old scan)
        self.logger.debug("Pre-fetching existing file list for parallel scan...")

        db_comics = (
            self.db.query(Comic)
            .join(Volume)
            .join(Series)
            .filter(Series.library_id == self.library.id)
            .all()
        )

        existing_map = {c.file_path: c for c in db_comics}

        # --- Phase 1: Build task list with skip/import/update logic ---
        tasks = []
        scanned_paths_on_disk = set()
        skipped = 0

        for file_path in library_path.rglob("*"):

            if file_path.suffix.lower() not in self.supported_extensions:
                continue

            # --- CONTAINER RECONCILIATION ---
            # This runs for EVERY file, but the logic inside ensures it only
            # performs the disk-check once per folder.
            self._reconcile_sidecars(file_path, existing_map)

            file_path_str = str(file_path)
            scanned_paths_on_disk.add(file_path_str)

            file_mtime = os.path.getmtime(file_path)
            existing = existing_map.get(file_path_str)

            if existing:
                # unchanged file → skip unless force=True
                if not force and existing.file_modified_at and existing.file_modified_at >= file_mtime:
                    skipped += 1
                    continue

                # changed file → update
                tasks.append(file_path_str)

            else:
                # new file → import
                tasks.append(file_path_str)

        # Commit any sidecar updates found during discovery
        self.db.commit()

        # If nothing needs work, return early
        if not tasks:
            elapsed = round(time.time() - start_time, 2)
            return {
                "library": self.library.name,
                "imported": 0,
                "updated": 0,
                "deleted": 0,
                "errors": 0,
                "skipped": skipped,
                "elapsed": elapsed,
            }

        # 2. Start writer process
        # --- Queues ---
        result_queue = Queue()
        stats_queue = Queue()

        # --- Start writer ---
        writer_proc = multiprocessing.Process(
            target=metadata_writer,
            args=(result_queue, stats_queue, self.library.id),
            kwargs={"batch_size": 50}
        )
        writer_proc.start()

        # Determine Worker Count
        if worker_limit > 0:
            workers = worker_limit  # Respect the override (e.g., 1)
            if worker_limit == 1:
                self.logger.info(f"Using exactly 1 worker for metadata extraction (SERIAL MODE)")
            else:
                self.logger.info(f"Using exactly {worker_limit} worker(s) for metadata extraction")
        else:
            requested_workers = int(get_cached_setting("system.parallel_metadata_workers", 0))
            self.logger.info(f"Requested {'(Auto)' if requested_workers <= 0 else requested_workers} worker(s) for parallel metadata extraction")

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

        # Wait for summary
        summary = stats_queue.get()

        writer_proc.join()

        # --- Phase 3: Cleanup missing files ---
        deleted = self._cleanup_missing_files(scanned_paths_on_disk, existing_map)

        # Cleanup empty containers
        self.reading_list_service.cleanup_empty_lists()
        self.collection_service.cleanup_empty_collections()

        # Update library scan time
        self.library.last_scanned = datetime.now(timezone.utc)
        self.db.commit()

        elapsed = round(time.time() - start_time, 2)

        print(summary)

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

        # Iterate over the map of comics we knew about at start
        for file_path, comic in existing_map.items():
            if file_path not in scanned_paths_on_disk:
                self.logger.info(f"Removing deleted comic: {comic.filename}")
                self.db.delete(comic)
                deleted += 1

        if deleted > 0:
            self.db.commit()

        return deleted

    def _reconcile_sidecars(self, file_path: Path, existing_map: dict):
        """
        Syncs folder-level sidecars with Series/Volume models.
        Works even if individual comics are skipped.
        """
        folder_path = file_path.parent
        folder_str = str(folder_path)
        lib_path = Path(self.library.path)

        # If the comic is in the root, there is no 'Series' or 'Volume' folder to reconcile
        if folder_path == lib_path:
            return

        if folder_str in self.reconciled_folders:
            return

        # Identify which Series/Volume this folder belongs to using pre-fetched data
        # We look at the first comic we know about in this folder
        existing_comic = existing_map.get(str(file_path))
        if not existing_comic:
            return  # New comics will handle this during _import_comic

        # 1. Update Volume (Clear if missing)
        vol = existing_comic.volume
        disk_vol_summary = SidecarService.get_summary_from_disk(folder_path, "volume")
        if vol.summary_override != disk_vol_summary:
            vol.summary_override = disk_vol_summary
            self.logger.info(f"Sidecar: Updated Volume {vol.volume_number} summary.")

        # 2. Update Series (Clear if missing)
        series = vol.series
        # If flat structure, series_path is the same as folder_path
        # If nested, we look one level up
        series_path = folder_path if folder_path.parent == Path(self.library.path) else folder_path.parent
        disk_series_summary = SidecarService.get_summary_from_disk(series_path, "series")

        if series.summary_override != disk_series_summary:
            series.summary_override = disk_series_summary
            self.logger.info(f"Sidecar: Updated Series '{series.name}' summary.")

        self.reconciled_folders.add(folder_str)

