import time
import logging
import threading
from pathlib import Path
from sqlalchemy.orm import selectinload
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from app.database import SessionLocal
from app.models.library import Library
from app.services.scan_manager import scan_manager

from app.core.settings_loader import get_cached_setting


class LibraryEventHandler(FileSystemEventHandler):
    """
        Handles file system events for a specific library.
        Uses a 'Batching Window' strategy: The first event starts a timer.
        Subsequent events are ignored until the timer fires.
    """

    def __init__(self, library_id: int, batch_window_seconds: int = 600): # Default 10 mins
        self.library_id = library_id
        self.batch_window_seconds = batch_window_seconds
        self._timer = None
        self._lock = threading.Lock()
        self._stopped = False

        self.logger = logging.getLogger(__name__)

        # Files to completely ignore
        self.ignored_extensions = {'.webp', '.part', '.tmp', '.crdownload'}
        self.ignored_names = {'.ds_store', 'thumbs.db', 'desktop.ini'}
        # Directories to ignore
        self.ignored_dirs = {'storage', '.git', '__pycache__'}


    def stop(self):
        """Cancel any pending scan timers (used when disabling watch mode)"""
        with self._lock:
            self._stopped = True
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _trigger_scan(self):
        """Trigger the scan and reset the timer"""
        with self._lock:
            # If we were stopped while waiting, don't scan
            if self._stopped:
                return
            self._timer = None

        self.logger.info(f"Watcher: Batch window ended for Library {self.library_id}. Queuing scan...")
        scan_manager.add_task(self.library_id, force=False)

    def on_any_event(self, event):
        """Called on any file event (create, modify, move, delete)"""
        if event.is_directory:
            return

        path = Path(event.src_path)

        # --- FILTER NOISE ---
        # Ignore thumbnails and temp files
        if path.suffix.lower() in self.ignored_extensions:
            return

        # Ignore system files
        if path.name.lower() in self.ignored_names:
            return

        # Ignore internal storage/git folders if they somehow got into the watch path
        # Check if any part of the path matches ignored dirs
        if any(part in self.ignored_dirs for part in path.parts):
            return
        # -----------------------

        # Coalescing Logic (Batching)
        # If a timer is already running, do nothing (let it gather more changes).
        # If no timer, start one.
        with self._lock:
            if not self._stopped and not self._timer:

                self.logger.debug(f"Watcher: Change detected in Library {self.library_id} ({event.event_type}: {path.name}). Starting {self.batch_window_seconds}s batch window.")

                # Start timer
                self._timer = threading.Timer(self.batch_window_seconds, self._trigger_scan)
                self._timer.start()


class LibraryWatcher:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LibraryWatcher, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.logger = logging.getLogger(__name__)
        self.observer = Observer()
        self.watches = {}  # Map library_id -> watch_object
        self.is_running = False
        self._initialized = True

    def start(self):
        """Start the background observer thread"""
        if not self.is_running:
            self.refresh_watches()
            self.observer.start()
            self.is_running = True
            self.logger.info("Library Watcher Started")

    def stop(self):
        """Stop the observer"""
        if self.is_running:
            self.observer.stop()
            self.observer.join()
            self.is_running = False
            self.logger.info("Library Watcher Stopped")

    def refresh_watches(self):
        """Sync active watches with the Database"""
        db = SessionLocal()
        try:
            # 1. Get all libraries that should be watched
            libraries = (
                db.query(Library)
                .options(selectinload(Library.roots))
                .filter(Library.watch_mode == True)
                .all()
            )
            active_ids = {lib.id for lib in libraries}
            current_ids = set(self.watches.keys())

            # Fetch Dynamic Setting (Default 600s if missing)
            # We fetch it once per refresh so all new watches use the updated value.
            # (Existing watches won't update their timeout until they are restarted, which is fine)
            batch_window = get_cached_setting("scanning.batch_window", 600)

            # 2. Add new watches
            for lib in libraries:
                if lib.id not in self.watches:
                    active_root = lib.active_root
                    if active_root is None:
                        self.logger.error(f"Library {lib.id} ('{lib.name}') has no active root; skipping watch")
                        continue

                    try:
                        self.logger.info(f"Starting watch for: {active_root.path}")
                        handler = LibraryEventHandler(lib.id, int(batch_window))
                        watch = self.observer.schedule(handler, active_root.path, recursive=True)

                        # Store both so we can cancel the handler later
                        self.watches[lib.id] = (watch, handler)
                    except Exception as e:
                        self.logger.error(f"Failed to watch {active_root.path}: {e}")

            # 3. Remove old watches (if disabled in DB)
            for lib_id in current_ids:
                if lib_id not in active_ids:
                    self.logger.info(f"Stopping watch for Library {lib_id}")
                    watch, handler = self.watches[lib_id]

                    # Cancel any pending timer
                    handler.stop()

                    # Unschedule from watchdog
                    self.observer.unschedule(watch)
                    del self.watches[lib_id]

        finally:
            db.close()


# Global Instance
library_watcher = LibraryWatcher()