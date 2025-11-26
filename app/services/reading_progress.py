from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, List
from app.models import ReadingProgress, Comic, Volume

class ReadingProgressService:
    """
    Service for managing reading progress.
    Refactored to use 'Flush' instead of 'Commit' for better concurrency.
    """

    def __init__(self, db: Session, user_id: int = 1):
        self.db = db
        self.user_id = user_id

    def get_progress(self, comic_id: int) -> Optional[ReadingProgress]:
        """Get reading progress for a comic"""
        return self.db.query(ReadingProgress).filter(
            ReadingProgress.user_id == self.user_id,
            ReadingProgress.comic_id == comic_id
        ).first()

    def update_progress(self, comic_id: int, current_page: int, total_pages: int = None) -> ReadingProgress:
        """
        Update reading progress for a comic.
        NOTE: Caller must run db.commit() to persist changes.
        """
        # Get or create progress record
        progress = self.get_progress(comic_id)

        if not progress:
            # Get total pages from comic if not provided
            if total_pages is None:
                comic = self.db.query(Comic).filter(Comic.id == comic_id).first()
                if not comic:
                    raise ValueError(f"Comic {comic_id} not found")
                total_pages = comic.page_count

            progress = ReadingProgress(
                user_id=self.user_id,
                comic_id=comic_id,
                current_page=current_page,
                total_pages=total_pages,
                completed=False
            )
            self.db.add(progress)
        else:
            # Update existing progress
            progress.current_page = current_page
            if total_pages is not None:
                progress.total_pages = total_pages
            progress.last_read_at = datetime.utcnow()

        # Check if completed (on last page)
        # Safe navigation for total_pages in case it's 0 or None
        t_pages = progress.total_pages if progress.total_pages else 0
        if current_page >= (t_pages - 1) and t_pages > 0:
            progress.completed = True
        else:
            progress.completed = False

        # CHANGED: Flush only. Checks constraints but doesn't write to disk.
        self.db.flush()

        return progress

    def mark_as_read(self, comic_id: int) -> ReadingProgress:
        """Mark a comic as completely read"""
        comic = self.db.query(Comic).filter(Comic.id == comic_id).first()
        if not comic:
            raise ValueError(f"Comic {comic_id} not found")

        progress = self.get_progress(comic_id)

        if not progress:
            progress = ReadingProgress(
                user_id=self.user_id,
                comic_id=comic_id,
                current_page=comic.page_count - 1,
                total_pages=comic.page_count,
                completed=True,
                last_read_at=datetime.utcnow()
            )
            self.db.add(progress)
        else:
            progress.current_page = progress.total_pages - 1
            progress.completed = True
            progress.last_read_at = datetime.utcnow()

        # CHANGED: Flush only
        self.db.flush()

        return progress

    def mark_as_unread(self, comic_id: int) -> None:
        """Remove reading progress (mark as unread)"""
        progress = self.get_progress(comic_id)

        if progress:
            self.db.delete(progress)
            # CHANGED: No commit here. Caller must commit.

    def get_recently_read(self, limit: int = 20) -> List[ReadingProgress]:
        """Get recently read comics"""
        return self.db.query(ReadingProgress).filter(
            ReadingProgress.user_id == self.user_id
        ).order_by(
            ReadingProgress.last_read_at.desc()
        ).limit(limit).all()

    def get_in_progress(self, limit: int = 20) -> List[ReadingProgress]:
        """Get comics currently being read (not completed)"""
        return self.db.query(ReadingProgress).filter(
            ReadingProgress.user_id == self.user_id,
            ReadingProgress.completed == False
        ).order_by(
            ReadingProgress.last_read_at.desc()
        ).limit(limit).all()

    def get_completed(self, limit: int = 20) -> List[ReadingProgress]:
        """Get completed comics"""
        return self.db.query(ReadingProgress).filter(
            ReadingProgress.user_id == self.user_id,
            ReadingProgress.completed == True
        ).order_by(
            ReadingProgress.last_read_at.desc()
        ).limit(limit).all()

    def get_series_progress(self, series_id: int) -> List[ReadingProgress]:
        """Get reading progress for all comics in a series"""
        return self.db.query(ReadingProgress).join(
            Comic
        ).join(
            Volume
        ).filter(
            ReadingProgress.user_id == self.user_id,
            Volume.series_id == series_id
        ).order_by(
            Comic.number
        ).all()