import pytest
from sqlalchemy.exc import IntegrityError

from app.models.tags import Character, Genre, Location, Team
from app.services.tags import TagService


TAG_CASES = [
    (Character, "get_or_create_character", "get_or_create_characters"),
    (Team, "get_or_create_team", "get_or_create_teams"),
    (Location, "get_or_create_location", "get_or_create_locations"),
    (Genre, "get_or_create_genre", "get_or_create_genres"),
]


@pytest.mark.parametrize(("model", "single_method", "plural_method"), TAG_CASES)
def test_tag_service_reuses_existing_tag_case_insensitively(
    db,
    model,
    single_method,
    plural_method,
):
    existing = model(name="Action")
    db.add(existing)
    db.flush()

    tag = getattr(TagService(db), single_method)("action")

    assert tag.id == existing.id
    assert tag.name == "Action"
    assert db.query(model).count() == 1


@pytest.mark.parametrize(("model", "single_method", "plural_method"), TAG_CASES)
def test_tag_service_deduplicates_tag_lists_case_insensitively(
    db,
    model,
    single_method,
    plural_method,
):
    service = TagService(db)

    tags = getattr(service, plural_method)("Action, action, ACTION, Adventure")
    same_tag = getattr(service, single_method)("aCtIoN")

    assert [tag.name for tag in tags] == ["Action", "Adventure"]
    assert same_tag.id == tags[0].id
    assert db.query(model).count() == 2


@pytest.mark.parametrize(("model", "single_method", "plural_method"), TAG_CASES)
def test_tag_name_unique_indexes_are_case_insensitive(
    db,
    model,
    single_method,
    plural_method,
):
    db.add(model(name="Action"))
    db.commit()

    db.add(model(name="action"))

    with pytest.raises(IntegrityError):
        db.commit()

    db.rollback()
