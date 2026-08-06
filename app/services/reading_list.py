import logging
from sqlalchemy.orm import Session, joinedload
from typing import Optional, Dict, Tuple
from app.models import ReadingList, ReadingListItem, Comic, Volume, Series
from app.models.cbl_source import CBLSource
from app.services.enrichment import EnrichmentService

class ReadingListService:
    def __init__(
        self,
        db: Session,
        *,
        allow_online_enrichment: bool = False,
        online_enrichment_lookup_limit: Optional[int] = None,
        online_enrichment_request_limit: int = 6,
        enrichment: Optional[EnrichmentService] = None,
    ):
        self.db = db
        self.list_cache: Dict[Tuple[str, str], Optional[ReadingList]] = {}
        self.logger = logging.getLogger(__name__)
        self.allow_online_enrichment = allow_online_enrichment
        self.online_enrichment_lookup_limit = online_enrichment_lookup_limit
        self.online_enrichment_attempts = 0
        self.enrichment = enrichment or EnrichmentService(
            allow_online=allow_online_enrichment,
            max_online_requests=online_enrichment_request_limit,
        )

    def _consume_online_enrichment_budget(self) -> bool:
        if not self.allow_online_enrichment:
            return False
        if self.online_enrichment_lookup_limit is not None:
            if self.online_enrichment_attempts >= self.online_enrichment_lookup_limit:
                return False
            self.online_enrichment_attempts += 1
        return True

    def get_or_create_reading_list(self, name: str, source: str = "comicinfo") -> Optional[ReadingList]:
        """
        Get-or-create a reading list scoped by (name, source) -- never by name
        alone. ComicInfo-derived list names are exactly the tagger's
        AlternateSeries value and are never renamed or disambiguated, so if a
        differently-sourced list (manual or cbl) already owns that exact name,
        there's no safe name left to create under. Returns None in that case;
        callers must skip rather than silently writing into someone else's list.
        """
        name = name.strip()
        cache_key = (name, source)

        if cache_key in self.list_cache:
            return self.list_cache[cache_key]

        reading_list = self.db.query(ReadingList).filter(
            ReadingList.name == name, ReadingList.source == source
        ).first()

        if not reading_list:
            conflict = self.db.query(ReadingList).filter(
                ReadingList.name == name, ReadingList.source != source
            ).first()
            if conflict:
                self.logger.warning(
                    "Skipping %s reading list '%s': name is already used by a %s list",
                    source, name, conflict.source,
                )
                self.list_cache[cache_key] = None
                return None

            reading_list = ReadingList(name=name, source=source)

            # Wikipedia/local-seed enrichment only makes sense for ComicInfo-derived
            # lists -- a CBL file supplies its own description, and a manual list is
            # the user's own to write.
            if source == "comicinfo":
                description = self.enrichment.get_description(
                    name,
                    allow_online=self._consume_online_enrichment_budget(),
                )
                if description:
                    reading_list.description = description

            self.db.add(reading_list)
            self.db.flush()
            self.logger.debug(f"Created reading list: {name}")

        self.list_cache[cache_key] = reading_list
        return reading_list

    def add_comic_to_list(self, comic: Comic, list_name: str, position: float):
        reading_list = self.get_or_create_reading_list(list_name)
        if reading_list is None:
            return

        existing = self.db.query(ReadingListItem).filter(
            ReadingListItem.reading_list_id == reading_list.id,
            ReadingListItem.comic_id == comic.id
        ).first()

        if existing:
            if existing.position != position:
                existing.position = position
                # No commit
        else:
            item = ReadingListItem(
                reading_list_id=reading_list.id,
                comic_id=comic.id,
                position=position
            )
            self.db.add(item)
            # No commit

    def remove_comic_from_all_lists(self, comic_id: int):
        self.db.query(ReadingListItem).filter(
            ReadingListItem.comic_id == comic_id
        ).delete()
        # No commit

    def remove_library_comics_from_all_lists(self, library_id: int) -> int:
        comic_ids_query = (
            self.db.query(Comic.id)
            .join(Volume, Comic.volume_id == Volume.id)
            .join(Series, Volume.series_id == Series.id)
            .filter(Series.library_id == library_id)
        )
        return self.db.query(ReadingListItem).filter(
            ReadingListItem.comic_id.in_(comic_ids_query)
        ).delete(synchronize_session=False)

    def _get_comicinfo_items_for_comic(self, comic_id: int) -> list[ReadingListItem]:
        """
        Items belonging to this comic's ComicInfo-derived ("source=comicinfo") list
        memberships only. Scoped this way so this method never touches "manual" or
        "cbl" memberships for the same comic -- a comic can belong to several
        reading lists at once (one per source), and each source only ever owns
        the items it created.
        """
        return (
            self.db.query(ReadingListItem)
            .join(ReadingList)
            .options(joinedload(ReadingListItem.reading_list))
            .filter(ReadingListItem.comic_id == comic_id, ReadingList.source == "comicinfo")
            .all()
        )

    def update_comic_reading_lists(self, comic: Comic, alternate_series: Optional[str],
                                   alternate_number: Optional[str]):
        target_name = alternate_series.strip() if alternate_series and alternate_series.strip() else None
        target_position = None

        if target_name and alternate_number:
            try:
                target_position = float(alternate_number)
            except (TypeError, ValueError):
                target_name = None
        else:
            target_name = None

        current_items = self._get_comicinfo_items_for_comic(comic.id)

        if not current_items:
            if target_name and target_position is not None:
                self.add_comic_to_list(comic, target_name, target_position)
            return

        if (
            target_name
            and target_position is not None
            and len(current_items) == 1
            and current_items[0].reading_list
            and current_items[0].reading_list.name == target_name
        ):
            if current_items[0].position != target_position:
                current_items[0].position = target_position
            return

        for item in current_items:
            self.db.delete(item)

        if target_name and target_position is not None:
            self.add_comic_to_list(comic, target_name, target_position)

    def cleanup_empty_lists(self):
        # This usually runs at the end of the scan, safe to run logic here
        # but let the scanner commit it.
        # Scoped to "comicinfo" only -- never "manual" (a user may deliberately
        # keep an empty one) and never "cbl" (an empty CBL list is a valid state,
        # e.g. freshly imported with no local matches yet; CBL list lifecycle is
        # owned entirely by CBLSourceService.rebuild()/delete(), not this generic
        # cleanup, since nothing here would rebuild it afterward -- callers like
        # metadata rehydrate invoke this without ever touching CBL sources).
        empty_lists = self.db.query(ReadingList).filter(
            ~ReadingList.items.any(), ReadingList.source == "comicinfo"
        ).all()
        for rl in empty_lists:
            self.db.delete(rl)
            # Invalidate cache if we delete
            cache_key = (rl.name, rl.source)
            if cache_key in self.list_cache:
                del self.list_cache[cache_key]

    def _unique_cbl_list_name(self, name: str, cbl_source_id: int) -> str:
        """
        Avoid colliding with an existing manual/comicinfo list name. Only checked
        at creation time -- once a CBL-derived list exists for a given source, its
        name is left alone on subsequent resyncs. Callers only invoke this before
        a ReadingList row for `cbl_source_id` exists yet, so any same-named row
        found here belongs to a different source (manual, comicinfo, or another
        CBL source) and counts as a collision.
        """
        candidate = name
        suffix = 2
        while self.db.query(ReadingList).filter(ReadingList.name == candidate).first():
            candidate = f"{name} (CBL)" if suffix == 2 else f"{name} (CBL {suffix})"
            suffix += 1
        return candidate

    def sync_cbl_list(
        self,
        cbl_source: CBLSource,
        name: str,
        description: Optional[str],
        ordered_comic_ids: list[int],
    ) -> ReadingList:
        """
        Create or update the single ReadingList owned by this CBL source, replacing
        its items with `ordered_comic_ids` (1-indexed position order). Scoped
        entirely by `source_cbl_id`, so this never touches "manual" or "comicinfo"
        memberships for the same comics.
        """
        name = name.strip()

        reading_list = self.db.query(ReadingList).filter(
            ReadingList.source_cbl_id == cbl_source.id
        ).first()

        if not reading_list:
            unique_name = self._unique_cbl_list_name(name, cbl_source.id)
            reading_list = ReadingList(name=unique_name, source="cbl", source_cbl_id=cbl_source.id)
            self.db.add(reading_list)
            self.db.flush()

        if description:
            reading_list.description = description

        self.db.query(ReadingListItem).filter(
            ReadingListItem.reading_list_id == reading_list.id
        ).delete(synchronize_session=False)

        for position, comic_id in enumerate(ordered_comic_ids, start=1):
            self.db.add(ReadingListItem(
                reading_list_id=reading_list.id,
                comic_id=comic_id,
                position=float(position),
            ))

        self.db.flush()
        return reading_list
