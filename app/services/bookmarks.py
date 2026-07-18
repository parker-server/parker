from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.bookmark import Bookmark


class BookmarkService:
    """Manage user bookmarks independently from reading progress."""

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def list_for_comic(self, comic_id: int) -> List[Bookmark]:
        return (
            self.db.query(Bookmark)
            .filter(
                Bookmark.user_id == self.user_id,
                Bookmark.comic_id == comic_id,
            )
            .order_by(Bookmark.page_index.asc())
            .all()
        )

    def get_bookmark(self, bookmark_id: int) -> Optional[Bookmark]:
        return (
            self.db.query(Bookmark)
            .filter(
                Bookmark.id == bookmark_id,
                Bookmark.user_id == self.user_id,
            )
            .first()
        )

    def save_bookmark(self, comic_id: int, page_index: int, label: Optional[str] = None) -> tuple[Bookmark, bool]:
        normalized_label = (label or "").strip() or None
        bookmark = (
            self.db.query(Bookmark)
            .filter(
                Bookmark.user_id == self.user_id,
                Bookmark.comic_id == comic_id,
                Bookmark.page_index == page_index,
            )
            .first()
        )

        if bookmark:
            if bookmark.label != normalized_label:
                bookmark.label = normalized_label
                bookmark.updated_at = datetime.now(timezone.utc)
                self.db.flush()
            return bookmark, False

        bookmark = Bookmark(
            user_id=self.user_id,
            comic_id=comic_id,
            page_index=page_index,
            label=normalized_label,
        )
        self.db.add(bookmark)
        self.db.flush()
        return bookmark, True

    def rename_bookmark(self, bookmark_id: int, label: Optional[str]) -> Bookmark:
        bookmark = self.get_bookmark(bookmark_id)
        if not bookmark:
            raise ValueError("Bookmark not found")

        bookmark.label = (label or "").strip() or None
        bookmark.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return bookmark

    def delete_bookmark(self, bookmark_id: int) -> Bookmark:
        bookmark = self.get_bookmark(bookmark_id)
        if not bookmark:
            raise ValueError("Bookmark not found")

        self.db.delete(bookmark)
        self.db.flush()
        return bookmark
