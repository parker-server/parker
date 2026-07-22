import logging
from lxml import etree
from typing import Optional, Dict, Any
import json

from sqlalchemy.orm import Session

from app.models import Comic, Volume, Series
from app.services.reading_list import ReadingListService
from app.services.collection import CollectionService

logger = logging.getLogger(__name__)


def parse_comicinfo(xml_content: bytes) -> Dict[str, Any]:
    """
    Parse ComicInfo.xml and return structured data
    XSD: https://github.com/anansi-project/comicinfo/blob/main/schema/v2.0/ComicInfo.xsd
    """
    try:
        root = etree.fromstring(xml_content)

        # Helper to get text or None
        def get_text(element_name: str) -> Optional[str]:
            elem = root.find(element_name)
            return elem.text if elem is not None and elem.text else None

        # Helper to parse rating safely
        def get_rating(element_name: str) -> Optional[float]:
            text = get_text(element_name)
            if not text:
                return None
            try:
                # Handle comma decimals (e.g. "4,5") and clean whitespace
                clean_val = text.replace(',', '.').strip()
                val = float(clean_val)

                # Clamp to 0-5 range per XSD
                return max(0.0, min(5.0, val))
            except ValueError:
                return None

        return {
            # Basic info
            'series': get_text('Series'),
            'number': get_text('Number'),
            'volume': get_text('Volume'),
            'title': get_text('Title'),
            'summary': get_text('Summary'),
            'count': get_text('Count'),
            'age_rating': get_text('AgeRating'),
            'lang': get_text('LanguageISO'),
            'community_rating': get_rating('CommunityRating'),

            # Date
            'year': get_text('Year'),
            'month': get_text('Month'),
            'day': get_text('Day'),

            # Credits
            'writer': get_text('Writer'),
            'penciller': get_text('Penciller'),
            'inker': get_text('Inker'),
            'colorist': get_text('Colorist'),
            'letterer': get_text('Letterer'),
            'cover_artist': get_text('CoverArtist'),
            'editor': get_text('Editor'),

            # Publishing
            'publisher': get_text('Publisher'),
            'imprint': get_text('Imprint'),
            'format': get_text('Format'),
            'series_group': get_text('SeriesGroup'),

            # Technical
            'page_count': get_text('PageCount'),
            'scan_information': get_text('ScanInformation'),

            # Tags (these come as comma-separated in the XML)
            'characters': get_text('Characters'),
            'teams': get_text('Teams'),
            'locations': get_text('Locations'),
            'genre': get_text('Genre'),

            # Reading lists
            'alternate_series': get_text('AlternateSeries'),
            'alternate_number': get_text('AlternateNumber'),
            'story_arc': get_text('StoryArc'),

            # Web link
            'web': get_text('Web'),

            # Store full XML for future use
            'raw_xml': xml_content.decode('utf-8')
        }
    except Exception as e:
        logger.error(f"Error parsing ComicInfo.xml: {e}")
        return {}


def rehydrate_library_metadata_from_cache(
    db: Session,
    library_id: int,
    *,
    rehydrate_reading_lists: bool,
    rehydrate_collections: bool,
    rehydrate_story_arcs: bool,
) -> Dict[str, Any]:
    summary = {
        "comics_scanned": 0,
        "source_metadata_missing": 0,
        "source_metadata_invalid": 0,
        "reading_lists_restored": 0,
        "collections_restored": 0,
        "story_arcs_restored": 0,
        "force_scan_recommended": False,
    }

    if not any([rehydrate_reading_lists, rehydrate_collections, rehydrate_story_arcs]):
        return summary

    reading_list_service = ReadingListService(db) if rehydrate_reading_lists else None
    collection_service = CollectionService(db) if rehydrate_collections else None

    comics = (
        db.query(Comic)
        .join(Volume, Comic.volume_id == Volume.id)
        .join(Series, Volume.series_id == Series.id)
        .filter(Series.library_id == library_id)
        .all()
    )

    for comic in comics:
        summary["comics_scanned"] += 1

        raw_metadata = {}
        metadata_state = None

        if not comic.metadata_json:
            metadata_state = "missing"
        else:
            try:
                parsed = json.loads(comic.metadata_json)
                if isinstance(parsed, dict):
                    raw_metadata = parsed
                else:
                    metadata_state = "invalid"
            except (TypeError, ValueError):
                metadata_state = "invalid"

        if metadata_state == "missing":
            summary["source_metadata_missing"] += 1
        elif metadata_state == "invalid":
            summary["source_metadata_invalid"] += 1

        if rehydrate_reading_lists:
            alternate_series = raw_metadata.get("alternate_series")
            alternate_number = raw_metadata.get("alternate_number")
            comic.alternate_series = alternate_series
            comic.alternate_number = alternate_number
            reading_list_service.update_comic_reading_lists(comic, alternate_series, alternate_number)
            if alternate_series and alternate_number:
                try:
                    float(alternate_number)
                    summary["reading_lists_restored"] += 1
                except (TypeError, ValueError):
                    pass

        if rehydrate_collections:
            series_group = raw_metadata.get("series_group")
            comic.series_group = series_group
            collection_service.update_comic_collections(comic, series_group)
            if series_group:
                summary["collections_restored"] += 1

        if rehydrate_story_arcs:
            story_arc = raw_metadata.get("story_arc")
            comic.story_arc = story_arc
            if story_arc:
                summary["story_arcs_restored"] += 1

    db.flush()

    if rehydrate_reading_lists:
        reading_list_service.cleanup_empty_lists()
    if rehydrate_collections:
        collection_service.cleanup_empty_collections()

    db.commit()

    summary["force_scan_recommended"] = (
        summary["source_metadata_missing"] > 0 or summary["source_metadata_invalid"] > 0
    )

    return summary
