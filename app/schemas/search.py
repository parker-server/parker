from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Union


# 1. The Request Models
class SearchFilter(BaseModel):
    """A single search filter"""
    field: Literal[
        'library', 'series', 'volume', 'number', 'title', 'publisher', 'imprint',
        'format', 'year', 'writer', 'penciller', 'inker', 'colorist',
        'letterer', 'cover_artist', 'editor', 'character', 'team',
        'location', 'collection', 'reading_list', 'series_group', 'story_arc'
    ]
    operator: Literal[
        'equal', 'not_equal',
        'contains', 'does_not_contain', 'must_contain',
        'is_empty', 'is_not_empty'
    ]
    # Value can be a single string/int or a list
    value: Optional[Union[str, int, List[str], List[int]]] = None


class SearchRequest(BaseModel):
    """Search request with filters"""
    match: Literal['any', 'all'] = 'all'
    filters: List[SearchFilter] = Field(default_factory=list)

    sort_by: Literal['created', 'year', 'series', 'title', 'page_count'] = 'created'
    sort_order: Literal['asc', 'desc'] = 'desc'

    limit: int = Field(default=50, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    context_library_id: Optional[int] = None


# 2. The Response Models (Added Back)
class ComicSearchItem(BaseModel):
    """A lightweight representation of a comic for search results"""
    id: int
    series: str
    volume: int
    number: str
    title: Optional[str] = None
    year: Optional[int] = None
    publisher: Optional[str] = None
    format: Optional[str] = None
    thumbnail_path: Optional[str] = None


class SearchResponse(BaseModel):
    """Search results"""
    total: int
    limit: int
    offset: int
    results: List[ComicSearchItem]