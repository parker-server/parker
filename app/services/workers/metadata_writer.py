def _item_root_context(
    item,
    *,
    default_library_root_id,
    default_library_root_path,
    root_paths_by_id,
):
    library_root_id = item.get("library_root_id", default_library_root_id)
    library_root_path = item.get("library_root_path")

    if library_root_id is not None:
        try:
            library_root_id = int(library_root_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Metadata item has invalid library root identity") from exc

    if root_paths_by_id and library_root_id is not None:
        library_root_path = root_paths_by_id.get(library_root_id)
        if library_root_path is None:
            raise ValueError("Metadata item references unknown library root")

    if library_root_path is None:
        library_root_path = default_library_root_path

    if library_root_id is None or library_root_path is None:
        raise ValueError("Metadata item is missing library root identity")

    return library_root_id, library_root_path


def _apply_metadata_batch(
    db,
    batch,
    existing_by_key,
    get_or_create_series,
    get_or_create_volume,
    tag_service,
    credit_service,
    reading_list_service,
    collection_service,
    library_root_id=None,
    library_root_path=None,
    root_paths_by_id=None,
    parse_reading_lists=True,
    parse_collections=True,
    parse_story_arcs=True,
):

    from pathlib import Path
    from app.models.comic import Comic
    from app.core.path_utils import compute_relative_path
    from datetime import datetime, timezone
    from inspect import Parameter, signature
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

    def _normalize_volume_number(raw_volume) -> int:
        """Normalize volume metadata, falling back to volume 1 on invalid input."""
        if raw_volume in (None, ""):
            return 1

        try:
            return int(str(raw_volume).strip())
        except (TypeError, ValueError):
            return 1

    def _accepts_positional_args(func, count: int) -> bool:
        try:
            parameters = list(signature(func).parameters.values())
        except (TypeError, ValueError):
            return True

        if any(parameter.kind == Parameter.VAR_POSITIONAL for parameter in parameters):
            return True

        positional_parameters = [
            parameter
            for parameter in parameters
            if parameter.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
        ]
        return len(positional_parameters) >= count

    get_or_create_series_accepts_root_path = _accepts_positional_args(get_or_create_series, 2)
    get_or_create_volume_accepts_root_path = _accepts_positional_args(get_or_create_volume, 4)

    def _get_or_create_series(name: str, item_library_root_path: str):
        if get_or_create_series_accepts_root_path:
            return get_or_create_series(name, item_library_root_path)
        return get_or_create_series(name)

    def _get_or_create_volume(series, volume_num: int, file_path: str, item_library_root_path: str):
        if get_or_create_volume_accepts_root_path:
            return get_or_create_volume(series, volume_num, file_path, item_library_root_path)
        return get_or_create_volume(series, volume_num, file_path)

    imported = 0
    updated = 0
    errors = 0
    skipped = 0
    error_details = []

    for item in batch:
        if item.get("error"):
            # Skip errored items
            errors += 1
            error_details.append({
                "file_path": item["file_path"],
                "message": item.get("message", "Unknown error")
            })
            continue

        file_path = item["file_path"]
        metadata = item["metadata"]
        mtime = item["mtime"]
        size = item["size"]
        updated_at = datetime.now(timezone.utc)
        try:
            item_library_root_id, item_library_root_path = _item_root_context(
                item,
                default_library_root_id=library_root_id,
                default_library_root_path=library_root_path,
                root_paths_by_id=root_paths_by_id,
            )
        except ValueError as exc:
            errors += 1
            error_details.append({
                "file_path": file_path,
                "message": str(exc),
            })
            continue

        # Always resolves for scanner-provided tasks: the scanner only sends
        # paths physically nested under the selected library root.
        relative_path = compute_relative_path(item_library_root_path, file_path)
        if relative_path is None:
            errors += 1
            error_details.append({
                "file_path": file_path,
                "message": "File is not under its assigned library root",
            })
            continue

        existing = existing_by_key.get((item_library_root_id, relative_path))

        # --- Determine Import vs Update ---
        if existing:
            comic = existing
            action = "update"
        else:
            comic = Comic()
            action = "import"

        # Get or create series (Uses Cache)
        # Robust 'Unknown' handling for Series Name to prevent NOT NULL errors
        # If metadata['series'] is None or "", default to "Unknown Series"
        series_name = metadata.get("series") or "Unknown Series"
        series = _get_or_create_series(series_name, item_library_root_path)

        # Get or create volume (Uses Cache)
        volume_num = _normalize_volume_number(metadata.get("volume"))
        volume = _get_or_create_volume(series, volume_num, file_path, item_library_root_path)
        comic.volume_id = volume.id

        comic.library_root_id = item_library_root_id
        comic.relative_path = relative_path

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
        if parse_collections:
            comic.series_group = metadata.get("series_group")
        comic.scan_information = metadata.get("scan_information")
        if parse_reading_lists:
            comic.alternate_series = metadata.get("alternate_series")
            comic.alternate_number = metadata.get("alternate_number")
        if parse_story_arcs:
            comic.story_arc = metadata.get("story_arc")
        comic.count = int(metadata.get("count")) if metadata.get("count") else None
        comic.metadata_json = json.dumps(metadata.get("raw_metadata", {}))
        comic.updated_at = updated_at

        # Now that all required fields are set, add new comics
        if action == "import":
            db.add(comic)

        # CRITICAL: flush before writing credits/tags/etc.
        db.flush()

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
        if parse_reading_lists:
            reading_list_service.update_comic_reading_lists(
                comic,
                metadata.get("alternate_series"),
                metadata.get("alternate_number")
            )

        # --- Collections ---
        if parse_collections:
            collection_service.update_comic_collections(
                comic,
                metadata.get("series_group")
            )

        # --- Touch Parent Series (Timestamp bubbling) ---
        series.updated_at = updated_at

        if action == "import":
            comic.is_dirty = True
            imported += 1
            existing_by_key[(item_library_root_id, relative_path)] = comic

        elif action == "update":
            # If the scanner sent it, we *know* it changed (or force=True)
            comic.is_dirty = True
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
        "error_details": error_details
    }

def metadata_writer(
    queue,
    stats_queue,
    library_id,
    batch_size=50,
    parse_reading_lists=True,
    parse_collections=True,
    parse_story_arcs=True,
):

    try:

        from app.database import SessionLocal, engine
        from app.models import Comic, Series, Volume, Library, LibraryRoot
        from app.services.tags import TagService
        from app.services.credits import CreditService
        from app.services.reading_list import ReadingListService
        from app.services.collection import CollectionService
        from app.services.sidecar_service import SidecarService
        from pathlib import Path

        engine.dispose()
        db = SessionLocal()

        # Get library roots for physical identity and sidecar boundaries.
        library = db.get(Library, library_id)

        library_roots = (
            db.query(LibraryRoot)
            .filter(LibraryRoot.library_id == library_id, LibraryRoot.is_active == True)
            .order_by(LibraryRoot.id)
            .all()
        )
        if not library_roots:
            raise ValueError(f"Library '{library.name}' has no active root configured")

        root_paths_by_id = {root.id: root.path for root in library_roots}
        default_root = library_roots[0] if len(library_roots) == 1 else None
        default_library_root_id = default_root.id if default_root else None
        default_library_root_path = default_root.path if default_root else None
        fallback_library_root_path = default_library_root_path or library_roots[0].path

        # Preload existing comics
        existing_comics = (
            db.query(Comic)
            .join(Volume)
            .join(Series)
            .filter(Series.library_id == library_id)
            .all()
        )
        existing_by_key = {(c.library_root_id, c.relative_path): c for c in existing_comics}

        # Local caches to reduce DB reads during the scan loop
        series_cache = {}
        volume_cache = {}

        tag_service = TagService(db)
        credit_service = CreditService(db)
        reading_list_service = ReadingListService(db)
        collection_service = CollectionService(db)

        batch = []

        def get_or_create_series(name: str, item_library_root_path: str | None = None):
            """Get existing series or create new one with Caching"""

            # 1. Check local cache
            if name in series_cache:
                return series_cache[name]

            # 2. Check Database
            series = db.query(Series).filter_by(name=name, library_id=library_id).first()

            if not series:
                # 3. Create new (Flush, don't commit)
                series = Series(name=name, library_id=library_id)

                # Boundary Protection: Don't check root or "Unknown"
                if name != "Unknown Series":
                    lib_path = Path(item_library_root_path or fallback_library_root_path)
                    series_path = lib_path / name
                    # Physical Guard: Ensure it's a valid subfolder, not the root
                    if series_path != lib_path and lib_path in series_path.parents:
                        series.summary_override = SidecarService.get_summary_from_disk(series_path, "series")

                db.add(series)
                db.flush()

            # 4. Add to cache
            series_cache[name] = series
            return series

        def get_or_create_volume(series, num, file_path_str: str, item_library_root_path: str | None = None):
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

                # --- BOUNDARY PROTECTION ---
                folder_path = Path(file_path_str).parent

                lib_path = Path(item_library_root_path or fallback_library_root_path)

                # Only look for a sidecar if the folder is NOT the containing library root.
                if folder_path != lib_path:
                    v.summary_override = SidecarService.get_summary_from_disk(folder_path, "volume")

                db.add(v)
                db.flush()


            volume_cache[key] = v
            return v

        processed = {"imported": 0, "updated": 0, "errors": 0, "skipped": 0, "error_details": []}

        while True:
            item = queue.get()
            if item is None:
                break

            batch.append(item)

            if len(batch) >= batch_size:
                stats = _apply_metadata_batch(
                    db, batch, existing_by_key,
                    get_or_create_series, get_or_create_volume,
                    tag_service, credit_service,
                    reading_list_service, collection_service,
                    parse_reading_lists=parse_reading_lists,
                    parse_collections=parse_collections,
                    parse_story_arcs=parse_story_arcs,
                    library_root_id=default_library_root_id,
                    library_root_path=default_library_root_path,
                    root_paths_by_id=root_paths_by_id,
                )
                for key in ("imported", "updated", "errors", "skipped"):
                    processed[key] += stats.get(key, 0)

                processed["error_details"].extend(stats["error_details"])

                batch.clear()

        # Commit remaining
        if batch:
            stats = _apply_metadata_batch(db, batch, existing_by_key,
                    get_or_create_series, get_or_create_volume,
                    tag_service, credit_service,
                    reading_list_service, collection_service,
                    parse_reading_lists=parse_reading_lists,
                    parse_collections=parse_collections,
                    parse_story_arcs=parse_story_arcs,
                    library_root_id=default_library_root_id,
                    library_root_path=default_library_root_path,
                    root_paths_by_id=root_paths_by_id,
            )
            for key in ("imported", "updated", "errors", "skipped"):
                processed[key] += stats.get(key, 0)

            processed["error_details"].extend(stats["error_details"])

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
            "error_details": [{"file_path": None, "message": str(e)}]
        })
    finally:
        try:
            db.close()
        except:
            pass
