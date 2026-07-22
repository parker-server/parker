from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.database import commit_with_retry


class _FakeDB:
    def __init__(self, side_effects):
        self._effects = list(side_effects)
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1
        effect = self._effects.pop(0)
        if effect is not None:
            raise effect


def _locked_error():
    return OperationalError("statement", {}, Exception("database is locked"))


def test_commit_with_retry_succeeds_after_transient_lock(monkeypatch):
    monkeypatch.setattr("app.database.time.sleep", MagicMock())
    db = _FakeDB([_locked_error(), _locked_error(), None])

    commit_with_retry(db, attempts=5, delay=0)

    assert db.commit_calls == 3


def test_commit_with_retry_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("app.database.time.sleep", MagicMock())
    db = _FakeDB([_locked_error()] * 5)

    with pytest.raises(OperationalError):
        commit_with_retry(db, attempts=5, delay=0)

    assert db.commit_calls == 5


def test_commit_with_retry_reraises_immediately_on_non_lock_error(monkeypatch):
    sleep_mock = MagicMock()
    monkeypatch.setattr("app.database.time.sleep", sleep_mock)
    other_error = OperationalError("statement", {}, Exception("no such table: foo"))
    db = _FakeDB([other_error])

    with pytest.raises(OperationalError):
        commit_with_retry(db, attempts=5, delay=0)

    assert db.commit_calls == 1
    sleep_mock.assert_not_called()
