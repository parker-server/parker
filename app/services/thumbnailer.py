import logging
from pathlib import Path
import multiprocessing
from multiprocessing import Queue
from typing import Tuple, Dict, Any, List
from sqlalchemy.orm import Session

from app.core.settings_loader import get_cached_setting
from app.database import SessionLocal, engine
from app.models.comic import Comic, Volume
from app.models.library import Library
from app.models.series import Series
from app.services.images import ImageService


def _apply_batch(db, batch, stats_queue):
    """
    Apply a batch of updates to the DB.
    This runs inside the dedicated Writer process.
    """
    from app.models.comic import Comic

    for item in batch:

        comic_id = item.get("comic_id")

        if item.get("error"):
            stats_queue.put({"comic_id": comic_id, "status": "error"})
            continue

        # Fetch object to update
        comic = db.query(Comic).get(comic_id)
        if not comic:
            stats_queue.put({"comic_id": comic_id, "status": "missing"})
            continue

        # Update fields
        comic.thumbnail_path = item.get("thumbnail_path")
        palette = item.get("palette")

        if palette:
            comic.color_primary = palette.get("primary")
            comic.color_secondary = palette.get("secondary")
            comic.color_palette = palette

        stats_queue.put({"comic_id": comic_id, "status": "processed"})

    # Commit the batch (Single Transaction)
    db.commit()


def _thumbnail_worker(task: Tuple[int, str]) -> Dict[str, Any]:
    """
    Pure CPU worker: generates thumbnail + palette.
    Does NOT touch the database.
    """
    comic_id, file_path = task
    # Import here to avoid issues after fork
    from app.services.images import ImageService

    image_service = ImageService()
    target_path = Path(f"./storage/cover/comic_{comic_id}.webp")

    try:
        result = image_service.process_cover(str(file_path), target_path)

        if not result.get("success"):
            return {
                "comic_id": comic_id,
                "error": True,
                "message": "Image processing failed"
            }

        return {
            "comic_id": comic_id,
            "thumbnail_path": str(target_path),
            "palette": result.get("palette"),
            "error": False,
        }

    except Exception as e:
        # Keep it small and serializable
        return {
            "comic_id": comic_id,
            "error": True,
            "message": str(e)
        }


def _thumbnail_writer(queue: Queue, stats_queue: Queue, batch_size: int = 25) -> None:
    """
    Dedicated writer process: reads worker results and applies DB updates.
    OPTIMIZED: batch_size lowered to 25 to prevent holding the lock too long.
    """

    # CRITICAL Dispose inherited connections
    engine.dispose()

    from app.database import SessionLocal

    db = SessionLocal()
    processed = 0
    errors = 0
    skipped = 0
    batch = []

    try:
        while True:
            # Block until an item is available
            item = queue.get()

            # Sentinel value means "All workers are done"
            if item is None:
                break

            batch.append(item)

            # If batch is full, write it
            if len(batch) >= batch_size:
                _apply_batch(db, batch, stats_queue)
                processed += sum(1 for i in batch if not i.get("error"))
                errors += sum(1 for i in batch if i.get("error"))
                batch.clear()

        # Flush remaining items
        if batch:
            _apply_batch(db, batch, stats_queue)
            processed += sum(1 for i in batch if not i.get("error"))
            errors += sum(1 for i in batch if i.get("error"))

    finally:


        # CRITICAL Close DB *BEFORE* signaling summary.
        # This guarantees the lock is released before the parent process wakes up.
        db.close()

        # Signal completion
        stats_queue.put({
            "summary": True,
            "processed": processed,
            "errors": errors,
            "skipped": skipped,
        })

class ThumbnailService:
    def __init__(self, db: Session, library_id: int = None):
        self.db = db
        self.library_id = library_id
        self.image_service = ImageService()
        self.logger = logging.getLogger(__name__)

    def process_series_thumbnails(self, series_id: int):
        """
        Force regenerate thumbnails for ALL comics in a series.
        Refactored: Delegates to the parallel engine for safety.
        Forces 1 worker to keep it 'serial-like' if you prefer, or
        let it use multiple cores to finish the series instantly.
        """

        # Using 2 workers is usually safe and twice as fast for a series or 1 for serial behavior
        return self.process_missing_thumbnails_parallel(
            force=True,
            series_id=series_id,
            worker_limit=1
        )


    def _get_target_comics(self, force: bool = False) -> List[Comic]:

        if not self.library_id:
            raise ValueError("Library ID required for library-wide processing")

        query = (
            self.db
            .query(Comic)
            .join(Comic.volume)
            .join(Series)
            .filter(Series.library_id == self.library_id)
        )

        if not force:
            # Smart Filter: Get comics missing thumbnails OR missing colors
            # This ensures we backfill colors for existing comics too.
            query = query.filter(
                (Comic.thumbnail_path == None) | (Comic.color_primary == None)
            )

        return query.all()

    def process_missing_thumbnails_parallel(self, force: bool = False, series_id: int = None, worker_limit: int = 0) -> Dict[str, int]:
        """
        Parallel thumbnail generation.
        The writer process handles batching automatically.
        """

        # 1. BUILD QUERY based on inputs
        if series_id:
            # Targeted Series Scan (Does not require self.library_id)
            #self.logger.info(f"Processing Series {series_id}")
            comics = (self.db.query(Comic)
                      .join(Volume)
                      .filter(Volume.series_id == series_id)
                      .all())
        elif self.library_id:
            # Library-Wide Scan (Uses existing helper)
            #self.logger.info(f"Processing thumbnails for Library {self.library_id}")
            comics = self._get_target_comics(force=force)
        else:
            # Error: Neither target provided
            raise ValueError("Either series_id OR initialized library_id is required")


        stats = {"processed": 0, "errors": 0, "skipped": 0}

        if not comics:
            return stats

        # Pre-filter "skipped" to avoid sending unnecessary work
        tasks: List[Tuple[int, str]] = []
        for comic in comics:
            has_thumb = comic.thumbnail_path and Path(str(comic.thumbnail_path)).exists()
            has_colors = comic.color_primary is not None

            if not force and has_thumb and has_colors:
                stats["skipped"] += 1
                continue

            tasks.append((comic.id, str(comic.file_path)))

        if not tasks:
            return stats

        # Queues
        result_queue: Queue = multiprocessing.Queue()
        stats_queue: Queue = multiprocessing.Queue()

        # Start Writer (Handles DB Updates)
        writer_proc = multiprocessing.Process(
            target=_thumbnail_writer,
            args=(result_queue, stats_queue),
            kwargs={'batch_size': 25}  # Safe batch size
        )
        writer_proc.start()

        # Determine Worker Count
        if worker_limit > 0:
            workers = worker_limit  # Respect the override (e.g., 1)

            if worker_limit == 1:
                self.logger.info(f"Using exactly 1 worker for thumbnail generation (SERIAL MODE)")
            else:
                self.logger.info(f"Using exactly {worker_limit} worker(s) for thumbnail generation")

        else:
            requested_workers = int(get_cached_setting("system.parallel_image_workers", 0))
            self.logger.info(f"Requested {'(Auto)' if requested_workers <= 0 else requested_workers} worker(s) for parallel thumbnail generation")

            if requested_workers <= 0:
                # AUTO MODE:
                # Use 50% of cores, with a minimum of 1.
                # This prevents system starvation when multiple web workers are active.
                total_cores = multiprocessing.cpu_count() or 1
                workers = max(1, total_cores // 2)
            else:
                max_cores = multiprocessing.cpu_count() or 1
                workers = min(requested_workers, max_cores)

            self.logger.info(f"Using {workers} worker(s) for parallel thumbnail generation")

        # Start Workers (CPU bound)
        with multiprocessing.Pool(processes=workers) as pool:
            for payload in pool.imap_unordered(_thumbnail_worker, tasks):
                # Send worker result to writer
                result_queue.put(payload)

        # All worker tasks done; tell writer to finish
        result_queue.put(None)

        # Wait for stats
        summary_received = False
        while not summary_received:
            item = stats_queue.get()
            if item.get("summary"):
                stats["processed"] += item.get("processed", 0)
                stats["errors"] += item.get("errors", 0)
                stats["skipped"] += item.get("skipped", 0)
                summary_received = True

        writer_proc.join()

        return stats


