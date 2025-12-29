from pathlib import Path
import time
import logging
import multiprocessing
from multiprocessing import Queue

from sqlalchemy.orm import Session

from app.core.settings_loader import get_cached_setting
from app.models.library import Library

from app.services.workers.metadata_worker import metadata_worker
from app.services.workers.metadata_writer import metadata_writer

class LibraryScanner:
    """Scans library directories and imports comics with batch processing"""

    def __init__(self, library: Library, db: Session):
        self.library = library
        self.db = db
        self.supported_extensions = ['.cbz', '.cbr']
        self.logger = logging.getLogger(__name__)


    def scan_parallel(self, force: bool = False, worker_limit: int = 0) -> dict:
        """
        Parallel metadata extraction + single-writer DB updates.
        """
        library_path = Path(self.library.path)

        if not library_path.exists():
            raise FileNotFoundError(f"Library path does not exist: {self.library.path}")

        start_time = time.time()

        # --- Phase 1: Build task list ---
        tasks = []
        scanned_paths_on_disk = set()

        for file_path in library_path.rglob("*"):
            if file_path.suffix.lower() in self.supported_extensions:
                file_path_str = str(file_path)
                scanned_paths_on_disk.add(file_path_str)

                # Always extract metadata in parallel
                tasks.append(file_path_str)

        if not tasks:
            return {"imported": 0, "updated": 0, "deleted": 0, "errors": 0, "elapsed": 0}

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

        # Start Workers (CPU bound)
        with multiprocessing.Pool(processes=workers) as pool:
            for payload in pool.imap_unordered(metadata_worker, tasks):
                result_queue.put(payload)

        # Signal writer to finish
        result_queue.put(None)

        # Wait for summary
        summary = stats_queue.get()

        writer_proc.join()

        # --- Phase 3: Cleanup missing files ---
        deleted = self._cleanup_missing_files(scanned_paths_on_disk, {})

        elapsed = round(time.time() - start_time, 2)

        print(summary)

        return {
            "library": self.library.name,
            "skipped": summary.get("skipped", 0),
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

