from sqlalchemy.orm import Session
import logging

from app.models.tags import Character, Team, Location
from app.models.credits import Person
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.reading_list import ReadingList
from app.models.collection import Collection

from app.services.enrichment import EnrichmentService
from app.services.images import ImageService


class MaintenanceService:
    def __init__(self, db: Session):
        self.db = db
        self.logger = logging.getLogger(__name__)
        self.enrichment = EnrichmentService()

    def cleanup_orphans(self, library_id: int = None) -> dict:
        """
        Delete metadata entities that are no longer associated with any comics.
        OPTIMIZED: Commits after each step to yield the DB write lock.
        OPTIMIZED: Only runs heavy 'Global Tag' cleanup if library_id is None.
        """
        stats = {
            "series": 0,
            "volumes": 0,
            "characters": 0,
            "teams": 0,
            "locations": 0,
            "people": 0,
            "empty_lists": 0,
            "empty_collections": 0
        }

        # 1. Clean Empty Volumes (No comics linked)
        # This is fast and scoped to the library if provided
        # We use synchronize_session=False for speed since we are in a batch operation
        vol_query = self.db.query(Volume).filter(~Volume.comics.any())
        if library_id:
            # Join Series to check library_id
            # We cannot use .join() with .delete(). We must use a subquery.
            # Subquery: Find all Series IDs belonging to this library
            series_subquery = self.db.query(Series.id).filter(Series.library_id == library_id)
            vol_query = vol_query.filter(Volume.series_id.in_(series_subquery))

        stats["volumes"] = vol_query.delete(synchronize_session=False)
        self.db.commit()  # Yield Lock

        # 2. Clean Empty Series
        series_query = self.db.query(Series).filter(~Series.volumes.any())
        if library_id:
            series_query = series_query.filter(Series.library_id == library_id)

        stats["series"] = series_query.delete(synchronize_session=False)
        self.db.commit()  # Yield Lock

        # --- HEAVY OPERATIONS BELOW ---
        # We ONLY run these if this is a Global Cleanup (library_id is None).
        # It is inefficient to check global tags after every single library scan.

        if library_id is None:

            self.logger.info("Performing deep global cleanup (Tags, People, Collections)...")

            # 3. Clean Tags (Characters)
            stats["characters"] = self.db.query(Character).filter(~Character.comics.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 4. Clean Teams
            stats["teams"] = self.db.query(Team).filter(~Team.comics.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 5. Clean Locations
            stats["locations"] = self.db.query(Location).filter(~Location.comics.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 6. Clean People
            stats["people"] = self.db.query(Person).filter(~Person.credits.any()).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            # 7. Clean Empty Containers
            stats["empty_lists"] = self.db.query(ReadingList).filter(~ReadingList.items.any()).filter(
                ReadingList.auto_generated == True).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

            stats["empty_collections"] = self.db.query(Collection).filter(~Collection.items.any()).filter(
                Collection.auto_generated == True).delete(synchronize_session=False)
            self.db.commit()  # Yield Lock

        else:
            self.logger.info(f"Skipping deep tag cleanup for scoped scan (Library {library_id})")

        return stats

    def refresh_reading_list_descriptions(self) -> dict:
        """Populate missing descriptions for auto-generated lists."""
        lists = self.db.query(ReadingList).filter(ReadingList.auto_generated == True).all()
        updated_count = 0

        for r_list in lists:
            description = self.enrichment.get_description(r_list.name)
            if description and description != r_list.description:
                r_list.description = description
                updated_count += 1

                # Batch commit every 50 to avoid holding lock too long
                if updated_count % 50 == 0:
                    self.db.commit()

        if updated_count > 0:
            self.db.commit()

        return {"updated": updated_count, "total_scanned": len(lists)}
    