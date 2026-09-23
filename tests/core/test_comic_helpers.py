from app.core.comic_helpers import get_smart_cover, get_smart_covers_by_series
from app.models.comic import Comic, Volume
from app.models.series import Series
from tests.factories import create_comic, create_library_with_root


def _seed_cover_series(db):
    """Series designed to exercise every rule get_smart_cover applies. Returns {name: (series_id, {label: comic_id})}."""
    library = create_library_with_root(db, "Cover Helper Library", "/tmp/cover-helper")
    root = library.active_root
    seeded = {}

    def add_series(name: str, volumes: list[list[dict]]):
        series = Series(name=name, library=library)
        db.add(series)
        db.flush()
        comic_ids = {}
        for volume_number, comics in enumerate(volumes, start=1):
            volume = Volume(series=series, volume_number=volume_number)
            db.add(volume)
            db.flush()
            for spec in comics:
                spec = dict(spec)
                label = spec.pop("label")
                path = f"{name}-v{volume_number}-{label}.cbz"
                comic = create_comic(db, volume, root, path, filename=path, **spec)
                comic_ids[label] = comic.id
        seeded[name] = (series.id, comic_ids)

    # #0, negative and .5 issues must lose to a regular issue even though they sort first
    add_series("Promo First", [[
        {"label": "zero", "number": "0", "year": 2000},
        {"label": "negative", "number": "-1", "year": 2000},
        {"label": "half", "number": "1.5", "year": 2000},
        {"label": "one", "number": "1", "year": 2001},
    ]])
    # No #1 at all: the promo #0 must still lose to the first regular issue
    add_series("No Issue One", [[
        {"label": "zero", "number": "0", "year": 2000},
        {"label": "two", "number": "2", "year": 2001},
        {"label": "three", "number": "3", "year": 2002},
    ]])
    # No strict candidates at all -> falls back to the first issue by number
    add_series("Annuals Only", [[
        {"label": "two", "number": "2", "format": "annual", "year": 2001},
        {"label": "one", "number": "1", "format": "annual", "year": 2000},
    ]])
    # Only a #0 exists -> the fallback still picks it
    add_series("Only Zero", [[{"label": "zero", "number": "0", "year": 2000}]])
    # Known reverse-numbering title -> highest issue wins
    add_series("Countdown", [[
        {"label": "fifty-two", "number": "52", "year": 2007},
        {"label": "fifty", "number": "50", "year": 2007},
        {"label": "zero", "number": "0", "year": 2008},
    ]])
    # Two volumes with a #1 -> earliest year wins
    add_series("Year Tiebreak", [
        [{"label": "late", "number": "1", "year": 2005}],
        [{"label": "early", "number": "1", "year": 1999}],
    ])
    db.commit()
    return seeded


def _base_query(db):
    return db.query(Comic).join(Volume, Volume.id == Comic.volume_id).join(Series, Series.id == Volume.series_id)


def test_smart_covers_by_series_matches_get_smart_cover(db):
    seeded = _seed_cover_series(db)
    series_names = {series_id: name for name, (series_id, _) in seeded.items()}

    batched = get_smart_covers_by_series(_base_query(db), series_names)

    assert set(batched) == set(series_names)
    for name, (series_id, _) in seeded.items():
        expected = get_smart_cover(_base_query(db).filter(Series.id == series_id), series_name=name)
        assert batched[series_id].id == expected.id, name


def test_smart_covers_by_series_picks_expected_covers(db):
    seeded = _seed_cover_series(db)
    series_names = {series_id: name for name, (series_id, _) in seeded.items()}

    batched = get_smart_covers_by_series(_base_query(db), series_names)

    def picked(name: str) -> int:
        return batched[seeded[name][0]].id

    assert picked("Promo First") == seeded["Promo First"][1]["one"]
    assert picked("No Issue One") == seeded["No Issue One"][1]["two"]
    assert picked("Annuals Only") == seeded["Annuals Only"][1]["one"]
    assert picked("Only Zero") == seeded["Only Zero"][1]["zero"]
    assert picked("Countdown") == seeded["Countdown"][1]["fifty-two"]
    assert picked("Year Tiebreak") == seeded["Year Tiebreak"][1]["early"]


def test_smart_covers_by_series_exposes_thumbnail_fields_and_skips_empty_series(db):
    seeded = _seed_cover_series(db)
    empty = Series(name="Empty Series", library_id=db.query(Series).first().library_id)
    db.add(empty)
    db.commit()
    promo_id = seeded["Promo First"][0]

    batched = get_smart_covers_by_series(_base_query(db), {promo_id: "Promo First", empty.id: "Empty Series"})

    assert set(batched) == {promo_id}
    assert batched[promo_id].id == seeded["Promo First"][1]["one"]
    assert batched[promo_id].updated_at is not None


def test_smart_covers_by_series_query_count_is_bounded_by_numbering_direction(db, count_queries):
    seeded = _seed_cover_series(db)
    normal = {series_id: name for name, (series_id, _) in seeded.items() if name != "Countdown"}
    everything = {series_id: name for name, (series_id, _) in seeded.items()}

    with count_queries() as statements:
        get_smart_covers_by_series(_base_query(db), normal)
    assert len(statements) == 1

    # Reverse-numbered series sort the other way, so they cost one more query -- never one per series
    with count_queries() as statements:
        get_smart_covers_by_series(_base_query(db), everything)
    assert len(statements) == 2
