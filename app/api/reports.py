from fastapi import APIRouter, Depends
from sqlalchemy import func, case, Integer, desc, or_, tuple_
from typing import List, Annotated

from app.api.deps import SessionDep, AdminUser, PaginationParams, PaginatedResponse
from app.models.comic import Comic, Volume
from app.models.series import Series
from app.models.library import Library
from app.core.comic_helpers import get_format_filters

router = APIRouter()


@router.get("/missing", response_model=PaginatedResponse, name="missing_issues")
async def get_missing_issues_report(
        db: SessionDep,
        user: AdminUser,
        params: Annotated[PaginationParams, Depends()]
):
    """
    Returns a paginated list of volumes with missing issues.
    """

    # 1. Define Filters
    is_plain, is_annual, is_special = get_format_filters()

    # 2. Aggregation Query (Fast filter)
    candidates = (
        db.query(
            Volume.id,
            Volume.volume_number,
            Series.id.label("series_id"),
            Series.name.label("series_name"),
            Library.name.label("library_name"),
            func.max(Comic.count).label("expected_count"),
            func.count(case((is_plain, 1))).label("plain_count"),
            func.count(case((is_annual, 1))).label("annual_count"),
            func.count(case((is_special, 1))).label("special_count")
        )
        .join(Volume.series)
        .join(Series.library)
        .join(Comic, Comic.volume_id == Volume.id)
        .group_by(Volume.id)
        .having(
            (func.max(Comic.count) > 0) &
            (func.max(Comic.count) > func.count(case((is_plain, 1))))
        )
        .all()
    )

    full_report = []

    # 3. Process Candidates
    for row in candidates:
        is_standalone = (row.plain_count == 0 and (row.annual_count > 0 or row.special_count > 0))
        if is_standalone:
            continue

        existing_numbers = db.query(func.cast(Comic.number, Integer)) \
            .filter(Comic.volume_id == row.id) \
            .filter(is_plain) \
            .all()

        existing_set = set(r[0] for r in existing_numbers if r[0] is not None)

        has_zero = 0 in existing_set
        expected_range = range(0, row.expected_count) if has_zero else range(1, row.expected_count + 1)
        expected_set = set(expected_range)

        missing_set = expected_set - existing_set

        if missing_set:
            missing_list = sorted(list(missing_set))
            formatted_ranges = format_ranges(missing_list)

            full_report.append({
                "library": row.library_name,
                "series": row.series_name,
                "series_id": row.series_id,
                "volume_id": row.id,
                "volume": row.volume_number,
                "missing": formatted_ranges,
                "missing_count": len(missing_list),
                "owned": f"{row.plain_count} / {row.expected_count}"
            })

    # 4. Sort and Paginate (In-Memory Slicing)
    # We sort by Library -> Series -> Volume
    sorted_report = sorted(full_report, key=lambda x: (x['library'], x['series'], x['volume']))

    total = len(sorted_report)
    start = params.skip
    end = start + params.size

    # Slice the list for the requested page
    paginated_items = sorted_report[start:end]

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": paginated_items
    }


@router.get("/storage/libraries", name="library_storage")
async def get_library_storage_report(db: SessionDep, user: AdminUser):
    """
    Breakdown of storage usage per Library.
    """
    stats = (
        db.query(
            Library.name,
            func.count(Series.id.distinct()).label("series_count"),
            func.count(Comic.id).label("issue_count"),
            func.sum(Comic.file_size).label("total_bytes")
        )
        .join(Series, Series.library_id == Library.id)
        .join(Comic, Comic.volume_id == Volume.id)
        .join(Volume, Volume.series_id == Series.id)
        .group_by(Library.name)
        .order_by(desc("total_bytes"))
        .all()
    )

    return [
        {
            "library": row.name,
            "series_count": row.series_count,
            "issue_count": row.issue_count,
            "size_bytes": row.total_bytes or 0,
            # Calculate Avg size per issue to spot "bloated" libraries
            "avg_issue_mb": round((row.total_bytes / row.issue_count) / 1024 / 1024, 1) if row.issue_count else 0
        }
        for row in stats
    ]


@router.get("/storage/series", name="series_storage")
async def get_series_storage_report(db: SessionDep, user: AdminUser, limit: int = 20):
    """
    Top 20 'Heaviest' Series by disk size.
    """
    stats = (
        db.query(
            Series.id,
            Series.name,
            Library.name.label("library_name"),
            func.count(Comic.id).label("issue_count"),
            func.sum(Comic.file_size).label("total_bytes")
        )
        .join(Library, Library.id == Series.library_id)
        .join(Volume, Volume.series_id == Series.id)
        .join(Comic, Comic.volume_id == Volume.id)
        .group_by(Series.id)
        .order_by(desc("total_bytes"))
        .limit(limit)
        .all()
    )

    return [
        {
            "id": row.id,
            "name": row.name,
            "library": row.library_name,
            "issues": row.issue_count,
            "size_bytes": row.total_bytes or 0
        }
        for row in stats
    ]


@router.get("/storage/formats", name="format")
async def get_format_report(db: SessionDep, user: AdminUser):
    """
    Breakdown of file formats (CBZ, CBR, PDF, etc).
    Useful for identifying performance bottlenecks (CBR/RAR is slower).
    """
    # We derive format from file extension if the 'format' column isn't reliable,
    # but 'comic.filename' suffix is the source of truth.
    # SQLite doesn't have a clean "Right" string function, so we use LIKE

    formats = {
        "CBZ (Zip)": db.query(Comic).filter(Comic.filename.ilike('%.cbz')).count(),
        "CBR (Rar)": db.query(Comic).filter(Comic.filename.ilike('%.cbr')).count(),
        "PDF": db.query(Comic).filter(Comic.filename.ilike('%.pdf')).count(),
        "EPUB": db.query(Comic).filter(Comic.filename.ilike('%.epub')).count(),
    }

    # Calculate Total Size per format would be slower, counts are instant.
    return [{"format": k, "count": v} for k, v in formats.items() if v > 0]


def format_ranges(numbers: List[int]) -> str:
    if not numbers: return ""
    ranges = []
    start = numbers[0]
    prev = numbers[0]
    for x in numbers[1:]:
        if x != prev + 1:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = x
        prev = x
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


@router.get("/metadata", name="metadata_health")
async def get_metadata_health_report(db: SessionDep, user: AdminUser):
    """
    Analyzes library for missing metadata fields and potential issues.
    """
    total_comics = db.query(Comic).count()
    if total_comics == 0:
        return {"score": 100, "issues": []}

    # Define the checks
    # Each check is a tuple: (Label, SQLAlchemy Filter, Severity)
    checks = [
        (
            "Missing Summary",
            or_(Comic.summary == None, Comic.summary == ""),
            "warning"
        ),
        (
            "Missing Release Year",
            Comic.year == None,
            "warning"
        ),
        (
            "Missing Publisher",
            or_(Comic.publisher == None, Comic.publisher == ""),
            "info"
        ),
        (
            "Missing Page Count (or 0)",
            or_(Comic.page_count == None, Comic.page_count == 0),
            "error"
        ),
        (
            "Suspect Low Page Count (< 3)",
            (Comic.page_count > 0) & (Comic.page_count < 3),
            "error"
        ),
        (
            "Missing Web/Wiki Link",
            or_(Comic.web == None, Comic.web == ""),
            "info"
        )
    ]

    report = []

    for label, criteria, severity in checks:
        # Count offenders
        count = db.query(Comic).filter(criteria).count()

        if count > 0:
            # Get a sample of up to 5 items for the preview
            sample = db.query(Comic).filter(criteria).limit(5).all()
            sample_data = [f"{c.series_group or c.volume.series.name} #{c.number}" for c in sample]

            report.append({
                "label": label,
                "count": count,
                "percentage": round((count / total_comics) * 100, 1),
                "severity": severity,
                "sample": sample_data
            })

    # Calculate an arbitrary "Health Score" (100 - penalties)
    # Weights: Error=5, Warning=2, Info=0.5
    penalty = 0
    for item in report:
        weight = 5 if item['severity'] == 'error' else (2 if item['severity'] == 'warning' else 0.5)
        # Cap penalty per category to avoid negative scores from one bad batch
        cat_penalty = min(20, (item['count'] / total_comics) * 100 * weight)
        penalty += cat_penalty

    score = max(0, min(100, 100 - penalty))

    return {
        "overall_score": int(score),
        "total_comics": total_comics,
        "details": report
    }


@router.get("/duplicates", response_model=PaginatedResponse, name="duplicate")
async def get_duplicate_report(
        db: SessionDep,
        user: AdminUser,
        params: Annotated[PaginationParams, Depends()]
):
    """
    Finds comics that have the same Volume, Number, AND Format.
    """
    # 1. Find keys that have duplicates
    # We group by Volume, Number, AND Format to distinguish Annuals from regular issues.
    dupe_keys = (
        db.query(Comic.volume_id, Comic.number, Comic.format)
        .group_by(Comic.volume_id, Comic.number, Comic.format)
        .having(func.count(Comic.id) > 1)
        .all()
    )

    if not dupe_keys:
        return {
            "total": 0,
            "page": params.page,
            "size": params.size,
            "items": []
        }

    # 2. Fetch the actual comic details for those keys
    # We use a tuple_ IN clause to match all 3 fields at once
    comics = (
        db.query(Comic)
        .join(Comic.volume)
        .join(Volume.series)
        .join(Series.library)
        .filter(
            tuple_(Comic.volume_id, Comic.number, Comic.format).in_(dupe_keys)
        )
        .order_by(Series.name, Comic.volume_id, Comic.number)
        .all()
    )

    # 3. Group them in Python
    grouped_report = {}

    for c in comics:
        # Create a unique key for grouping
        # We include format in the key now so they don't merge
        key = f"{c.volume_id}_{c.number}_{c.format}"

        if key not in grouped_report:
            grouped_report[key] = {
                "series": c.volume.series.name,
                "volume": c.volume.volume_number,
                "number": c.number,
                "format": c.format,
                "library": c.volume.series.library.name,
                "files": []
            }

        grouped_report[key]["files"].append({
            "id": c.id,
            "filename": c.filename,
            "path": c.file_path,
            "size_bytes": c.file_size,
            "created_at": c.created_at
        })

    # 4. Sort and Paginate (In-Memory)
    full_list = sorted(list(grouped_report.values()), key=lambda x: (x['library'], x['series'], x['number']))

    total = len(full_list)
    start = params.skip
    end = start + params.size

    paginated_items = full_list[start:end]

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": paginated_items
    }


@router.get("/corrupt", response_model=PaginatedResponse, name="corrupt_files")
async def get_corrupt_files_report(
        db: SessionDep,
        user: AdminUser,
        params: Annotated[PaginationParams, Depends()]
):
    """
    Finds comics with suspiciously low page counts (1 or 2 pages).
    Usually indicates corrupted files, promo images, or bad rips.
    """

    # Logic: Page count is 1 or 2. (0 usually means 'unscanned', so we skip those)
    criteria = (Comic.page_count > 0) & (Comic.page_count < 3)

    query = (
        db.query(Comic)
        .join(Comic.volume)
        .join(Volume.series)
        .join(Series.library)
        .filter(criteria)
        .order_by(Comic.page_count, Comic.file_size)  # Smallest/Emptyest first
    )

    total = query.count()

    comics = query.offset(params.skip).limit(params.size).all()

    items = []
    for c in comics:
        items.append({
            "id": c.id,
            "library": c.volume.series.library.name,
            "series": c.volume.series.name,
            "volume": c.volume.volume_number,
            "number": c.number,
            "title": c.title,
            "page_count": c.page_count,
            "file_size": c.file_size,
            "path": c.file_path,
            "filename": c.filename
        })

    return {
        "total": total,
        "page": params.page,
        "size": params.size,
        "items": items
    }
