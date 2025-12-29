def _apply_metadata_batch(
    db,
    batch,
    existing_map,
    get_or_create_series,
    get_or_create_volume,
    tag_service,
    credit_service,
    reading_list_service,
    collection_service
):
    from pathlib import Path
    from app.models.comic import Comic
    from datetime import datetime, timezone
    import json

    def _normalize_number(number: str) -> str:
        """Normalize weird comic numbers"""
        if not number:
            return number

        # Handle "½" -> "0.5"
        if number == "½" or number == "1/2":
            return "0.5"

        # Handle "-1" (ensure it stays -1, though our casting handles it)
        # Handle variants if needed in future

        return number

    imported = 0
    updated = 0
    errors = 0
    skipped = 0


    for item in batch:
        if item.get("error"):
            # Skip errored items
            errors += 1
            continue

        file_path = item["file_path"]
        metadata = item["metadata"]
        mtime = item["mtime"]
        size = item["size"]
        updated_at = datetime.now(timezone.utc)


        existing = existing_map.get(file_path)

        # --- Determine Import vs Update ---
        if existing:
            comic = existing
            action = "update"
        else:
            comic = Comic(file_path=file_path)
            db.add(comic)
            action = "import"

        # Get or create series (Uses Cache)
        # Robust 'Unknown' handling for Series Name to prevent NOT NULL errors
        # If metadata['series'] is None or "", default to "Unknown Series"
        series_name = metadata.get("series") or "Unknown Series"
        series = get_or_create_series(series_name)

        # Get or create volume (Uses Cache)
        volume_num = int(metadata.get("volume", 1)) if metadata.get("volume") else 1
        volume = get_or_create_volume(series, volume_num)

        comic.volume_id = volume.id

        # --- Basic fields ---
        comic.file_modified_at = mtime
        comic.file_size = size
        comic.page_count = metadata["page_count"]

        # Normalize number
        raw_number = metadata.get("number")
        comic.number = _normalize_number(raw_number)

        comic.filename = Path(file_path).name
        comic.title = metadata.get("title")
        comic.summary = metadata.get("summary")
        comic.year = int(metadata.get("year")) if metadata.get("year") else None
        comic.month = int(metadata.get("month")) if metadata.get("month") else None
        comic.day = int(metadata.get("day")) if metadata.get("day") else None
        comic.web = metadata.get("web")
        comic.notes = metadata.get("notes")
        comic.age_rating = metadata.get("age_rating")
        comic.language_iso = metadata.get("lang")
        comic.community_rating = metadata.get("community_rating")
        comic.publisher = metadata.get("publisher")
        comic.imprint = metadata.get("imprint")
        comic.format = metadata.get("format")
        comic.series_group = metadata.get("series_group")
        comic.scan_information = metadata.get("scan_information")
        comic.alternate_series = metadata.get("alternate_series")
        comic.alternate_number = metadata.get("alternate_number")
        comic.story_arc = metadata.get("story_arc")
        comic.count = int(metadata.get("count")) if metadata.get("count") else None
        comic.metadata_json = json.dumps(metadata.get("raw_metadata", {}))
        comic.updated_at = updated_at
        comic.is_dirty = True  # Mark for thumbnailer

        # --- Credits ---
        credit_service.add_credits_to_comic(comic, metadata)

        # --- Tags ---
        comic.characters.clear()
        comic.teams.clear()
        comic.locations.clear()
        comic.genres.clear()

        if metadata.get("characters"):
            comic.characters = tag_service.get_or_create_characters(metadata["characters"])
        if metadata.get("teams"):
            comic.teams = tag_service.get_or_create_teams(metadata["teams"])
        if metadata.get("locations"):
            comic.locations = tag_service.get_or_create_locations(metadata["locations"])
        if metadata.get("genre"):
            comic.genres = tag_service.get_or_create_genres(metadata["genre"])

        # --- Reading Lists ---
        reading_list_service.update_comic_reading_lists(
            comic,
            metadata.get("alternate_series"),
            metadata.get("alternate_number")
        )

        # --- Collections ---
        collection_service.update_comic_collections(
            comic,
            metadata.get("series_group")
        )

        # --- Touch Parent Series (Timestamp bubbling) ---
        series.updated_at = updated_at

        # --- Update existing_map if new ---
        if action == "import":
            existing_map[file_path] = comic
            imported += 1
        else:
            updated += 1

        # Flush but do not commit
        db.flush()

    # Commit entire batch
    db.commit()

    return {
        "imported": imported,
        "updated": updated,
        "errors": errors,
        "skipped": skipped,
    }

def metadata_writer(queue, stats_queue, library_id, batch_size=50):

    try:

        from app.database import SessionLocal, engine
        from app.models import Comic, Series, Volume
        from app.services.tags import TagService
        from app.services.credits import CreditService
        from app.services.reading_list import ReadingListService
        from app.services.collection import CollectionService

        engine.dispose()
        db = SessionLocal()

        # Preload existing comics
        existing = {
            c.file_path: c
            for c in db.query(Comic)
                .join(Volume)
                .join(Series)
                .filter(Series.library_id == library_id)
                .all()
        }

        # Local caches to reduce DB reads during the scan loop
        series_cache = {}
        volume_cache = {}

        tag_service = TagService(db)
        credit_service = CreditService(db)
        reading_list_service = ReadingListService(db)
        collection_service = CollectionService(db)

        batch = []

        def get_or_create_series(name: str):
            """Get existing series or create new one with Caching"""

            # 1. Check local cache
            if name in series_cache:
                return series_cache[name]

            # 2. Check Database
            series = db.query(Series).filter_by(name=name, library_id=library_id).first()

            if not series:
                # 3. Create new (Flush, don't commit)
                series = Series(name=name, library_id=library_id)
                db.add(series)
                db.flush()

            # 4. Add to cache
            series_cache[name] = series
            return series

        def get_or_create_volume(series, num):
            """Get existing volume or create new one with Caching"""

            # Composite key for cache
            key = f"{series.id}_{num}"

            if key in volume_cache:
                return volume_cache[key]

            # 2. Check Database
            v = db.query(Volume).filter_by(series_id=series.id, volume_number=num).first()
            if not v:
                # 3. Create new (Flush, don't commit)
                v = Volume(series_id=series.id, volume_number=num)
                db.add(v)
                db.flush()


            volume_cache[key] = v
            return v

        processed = {"imported": 0, "updated": 0, "errors": 0, "skipped": 0}

        while True:
            item = queue.get()
            if item is None:
                break

            batch.append(item)

            if len(batch) >= batch_size:
                stats = _apply_metadata_batch(
                    db, batch, existing,
                    get_or_create_series, get_or_create_volume,
                    tag_service, credit_service,
                    reading_list_service, collection_service
                )
                for key in processed:
                    processed[key] += stats.get(key, 0)

                batch.clear()

        # Commit remaining
        if batch:
            stats = _apply_metadata_batch(db, batch, existing,
                    get_or_create_series, get_or_create_volume,
                    tag_service, credit_service,
                    reading_list_service, collection_service
            )
            for key in processed:
                processed[key] += stats.get(key, 0)

        #db.close()

        stats_queue.put({
            "summary": True,
            **processed
        })

    except Exception as e:
        # Send failure summary so scan_parallel can finish
        stats_queue.put({
            "summary": True,
            "imported": 0,
            "updated": 0,
            "errors": 1,
            "skipped": 0,
            "exception": str(e),
        })
    finally:
        try:
            db.close()
        except:
            pass
