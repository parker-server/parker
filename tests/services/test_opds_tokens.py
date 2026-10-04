from datetime import datetime, timedelta, timezone

from app.models.opds_token import UserOPDSToken
from app.services.opds_tokens import (
    OPDS_TOKEN_PREFIX,
    OPDS_TOKEN_USED_UPDATE_INTERVAL,
    authenticate_opds_token,
    create_opds_token,
    list_active_opds_tokens,
    mark_opds_token_used,
    revoke_opds_token,
)


def test_create_opds_token_stores_hash_and_returns_raw_token(db, normal_user):
    token, raw_token = create_opds_token(db, normal_user, " Moon+ Reader ")

    assert raw_token.startswith(OPDS_TOKEN_PREFIX)
    assert token.name == "Moon+ Reader"
    assert token.token_hash != raw_token
    assert token.token_hint.startswith(OPDS_TOKEN_PREFIX)
    assert token.token_hint.endswith(raw_token[-4:])

    stored = db.query(UserOPDSToken).filter_by(user_id=normal_user.id).one()
    assert stored.token_hash == token.token_hash


def test_authenticate_opds_token_returns_active_matching_token(db, normal_user):
    token, raw_token = create_opds_token(db, normal_user, "Tablet")

    matched = authenticate_opds_token(db, normal_user, raw_token)

    assert matched is not None
    assert matched.id == token.id


def test_opds_token_usage_and_revocation(db, normal_user):
    token, raw_token = create_opds_token(db, normal_user, "Reader")

    mark_opds_token_used(db, token)
    db.refresh(token)
    assert token.last_used_at is not None

    revoked = revoke_opds_token(db, normal_user, token.id)
    assert revoked is not None
    assert revoked.revoked_at is not None
    assert authenticate_opds_token(db, normal_user, raw_token) is None
    assert list_active_opds_tokens(db, normal_user) == []


def test_mark_opds_token_used_skips_recent_updates(db, normal_user):
    token, _ = create_opds_token(db, normal_user, "Reader")

    mark_opds_token_used(db, token)
    db.refresh(token)
    first_used_at = token.last_used_at

    mark_opds_token_used(db, token)
    db.refresh(token)

    assert token.last_used_at == first_used_at


def test_mark_opds_token_used_refreshes_stale_usage(db, normal_user):
    token, _ = create_opds_token(db, normal_user, "Reader")
    stale_used_at = datetime.now(timezone.utc) - OPDS_TOKEN_USED_UPDATE_INTERVAL - timedelta(seconds=1)
    token.last_used_at = stale_used_at
    db.add(token)
    db.commit()

    mark_opds_token_used(db, token)
    db.refresh(token)

    assert token.last_used_at != stale_used_at


def test_expired_opds_token_is_not_active(db, normal_user):
    token, raw_token = create_opds_token(
        db,
        normal_user,
        "Old Reader",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(token)
    db.commit()

    assert authenticate_opds_token(db, normal_user, raw_token) is None
    assert list_active_opds_tokens(db, normal_user) == []
