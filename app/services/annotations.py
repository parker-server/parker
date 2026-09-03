from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.models.annotation import Annotation


class AnnotationService:
    """Manage user annotations without coupling them to bookmarks or progress."""

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def list_for_comic(self, comic_id: int) -> List[Annotation]:
        return (
            self.db.query(Annotation)
            .filter(
                Annotation.user_id == self.user_id,
                Annotation.comic_id == comic_id,
            )
            .order_by(Annotation.page_index.asc(), Annotation.created_at.asc(), Annotation.id.asc())
            .all()
        )

    def list_for_page(self, comic_id: int, page_index: int) -> List[Annotation]:
        return (
            self.db.query(Annotation)
            .filter(
                Annotation.user_id == self.user_id,
                Annotation.comic_id == comic_id,
                Annotation.page_index == page_index,
            )
            .order_by(Annotation.created_at.asc(), Annotation.id.asc())
            .all()
        )

    def get_annotation(self, annotation_id: int) -> Optional[Annotation]:
        return (
            self.db.query(Annotation)
            .filter(
                Annotation.id == annotation_id,
                Annotation.user_id == self.user_id,
            )
            .first()
        )

    def create_annotation(
        self,
        *,
        comic_id: int,
        page_index: int,
        kind: str,
        title: Optional[str],
        body: Optional[str],
        color: str,
        anchor_json: dict[str, Any],
    ) -> Annotation:
        annotation = Annotation(
            user_id=self.user_id,
            comic_id=comic_id,
            page_index=page_index,
            kind=kind,
            title=title,
            body=body,
            color=color,
            anchor_json=anchor_json,
        )
        self.db.add(annotation)
        self.db.flush()
        return annotation

    def update_annotation(self, annotation_id: int, updates: dict[str, Any]) -> Annotation:
        annotation = self.get_annotation(annotation_id)
        if not annotation:
            raise ValueError("Annotation not found")

        for key, value in updates.items():
            setattr(annotation, key, value)

        annotation.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return annotation

    def delete_annotation(self, annotation_id: int) -> Annotation:
        annotation = self.get_annotation(annotation_id)
        if not annotation:
            raise ValueError("Annotation not found")

        self.db.delete(annotation)
        self.db.flush()
        return annotation
