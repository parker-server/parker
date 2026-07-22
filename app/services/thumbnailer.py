import logging
import time
from pathlib import Path
import multiprocessing
from multiprocessing import Queue
from queue import Empty
from typing import Tuple, Dict, Any, List
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.settings_loader import get_cached_setting
from app.database import SessionLocal, engine
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.services.images import ImageService

logger = logging.getLogger(__name__)


def _apply_batch(db, batch):
    """
    Apply a batch of updates to the DB and commit.
    Runs inside the dedicated Writer process. Returns per-item outcomes;
    callers report these to stats_queue/error_details themselves so a
    retried batch (after a rollback) doesn't double-report.
    """
    from app.models.comic import Comic

    outcomes = []

    for item in batch:
        comic_id = item.get("comic_id")

        if item.get("error"):
            outcomes.append({
                "comic_id": comic_id,
                "status": "error",
                "detail": {
                    "comic_id": comic_id,
                    "file_path": item.get("file_path"),
                    "message": item.get("message", "Unknown thumbnail error")
                }
            })
            continue

        # Fetch object to update
        comic = db.get(Comic, comic_id)
        if not comic:
            outcomes.append({
                "comic_id": comic_id,
                "status": "missing",
                "detail": {
                    "comic_id": comic_id,
                    "file_path": item.get("file_path"),
                    "message": "Comic not found in database"
                }
            })
            continue

        # Update fields
        comic.thumbnail_path = item.get("thumbnail_path")
        palette = item.get("palette")

        if palette:
            comic.color_primary = palette.get("primary")
            comic.color_secondary = palette.get("secondary")
            comic.color_palette = palette

        # Work is complete, reset the flag
        comic.is_dirty = False

        outcomes.append({"comic_id": comic_id, "status": "processed", "detail": None})

    # Commit the batch (Single Transaction)
    db.commit()
    return outcomes


def _apply_batch_with_retry(db, batch, attempts: int = 5, delay: float = 1.0):
    """
    Retry a batch application on transient SQLite 'database is locked' errors.

    A failed commit leaves the session's transaction unusable (SQLAlchemy
    raises PendingRollbackError on the next commit attempt) and rollback()
    discards the batch's pending in-memory changes along with it. So a
    lock failure requires re-running the whole batch (re-fetching rows and
    re-applying the field updates) on a rolled-back session, not just
    re-calling commit().
    """
    for attempt in range(attempts):
        try:
            return _apply_batch(db, batch)
        except OperationalError as e:
            db.rollback()
            if "locked" in str(e).lower() and attempt < attempts - 1:
                logger.warning(
                    f"DB Locked applying thumbnail batch (attempt {attempt + 1}/{attempts}). Retrying..."
                )
                time.sleep(delay)
                continue
            raise


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
                "file_path": file_path,
                "error": True,
                "message": "Image processing failed"
            }

        return {
            "comic_id": comic_id,
            "file_path": file_path,
            "thumbnail_path": str(target_path),
            "palette": result.get("palette"),
            "error": False,
        }

    except Exception as e:
        # Keep it small and serializable
        return {
            "comic_id": comic_id,
            "file_path": str(file_path),
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
    error_details = []
    batch = []

    def _flush_batch():
        nonlocal processed, errors

        outcomes = _apply_batch_with_retry(db, batch)
        for outcome in outcomes:
            stats_queue.put({"comic_id": outcome["comic_id"], "status": outcome["status"]})
            if outcome["status"] == "processed":
                processed += 1
            else:
                errors += 1
                if outcome["detail"]:
                    error_details.append(outcome["detail"])

        batch.clear()

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
                _flush_batch()

        # Flush remaining items
        if batch:
            _flush_batch()

    except Exception as e:
        # Batch retries were exhausted (or something else failed). Make this
        # explicit in the summary instead of silently under-reporting: the
        # batch currently in flight never got its outcomes counted above.
        logger.error(f"Thumbnail writer failed: {e}")
        errors += len(batch)
        error_details.append({
            "comic_id": None,
            "file_path": None,
            "message": f"Thumbnail writer aborted: {e}"
        })

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
            "error_details": error_details
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

        if force:
            return query.all()

        return query.filter(Comic.is_dirty == True).all()


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

            if not force and not comic.is_dirty and has_thumb and has_colors:
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

        summary_timeout = int(
            get_cached_setting("system.parallel_image_writer_summary_timeout_seconds", 180)
        )
        summary_timeout = max(10, summary_timeout)

        join_timeout = int(
            get_cached_setting("system.parallel_image_writer_join_timeout_seconds", 30)
        )
        join_timeout = max(1, join_timeout)

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

        sentinel_sent = False
        try:
            # Start Workers (CPU bound)
            with multiprocessing.Pool(processes=workers) as pool:
                for payload in pool.imap_unordered(_thumbnail_worker, tasks):
                    # Send worker result to writer
                    result_queue.put(payload)

            # All worker tasks done; tell writer to finish
            result_queue.put(None)
            sentinel_sent = True

            # Wait for stats, but do not block forever.
            summary_received = False
            while not summary_received:
                try:
                    item = stats_queue.get(timeout=summary_timeout)
                except Empty as exc:
                    raise TimeoutError(
                        f"Timed out after {summary_timeout}s waiting for thumbnail writer summary"
                    ) from exc

                if item.get("summary"):
                    stats["processed"] += item.get("processed", 0)
                    stats["errors"] += item.get("errors", 0)
                    stats["skipped"] += item.get("skipped", 0)
                    stats["error_details"] = item.get("error_details", [])
                    summary_received = True
        finally:
            if not sentinel_sent:
                try:
                    result_queue.put(None)
                except Exception:
                    pass

            if writer_proc.is_alive():
                writer_proc.join(timeout=join_timeout)

            if writer_proc.is_alive():
                self.logger.error("Thumbnail writer did not exit cleanly; terminating process.")
                writer_proc.terminate()
                writer_proc.join(timeout=5)

        return stats



