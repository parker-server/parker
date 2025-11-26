from fastapi import Query
from pydantic import BaseModel
from typing import TypeVar, Generic, Sequence

T = TypeVar("T")

class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        size: int = Query(50, ge=1, le=100, description="Items per page"),
    ):
        self.page = page
        self.size = size
        self.skip = (page - 1) * size

class PaginatedResponse(BaseModel, Generic[T]):
    total: int
    page: int
    size: int
    items: Sequence[T]