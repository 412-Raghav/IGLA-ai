"""Proves the db fixture actually rolls back.

Infrastructure, not features. If rollback silently fails, every other test
in this suite is suspect -- they would be seeing each other's rows and
passing for the wrong reason. These two run in file order: the first writes
and commits, the second asserts the row is gone.
"""

from models import User


def test_isolation_writes_and_commits(db):
    db.add(User(username="canary", password_hash="x"))
    db.commit()  # a real commit, exactly as chat_service will do
    assert db.query(User).filter_by(username="canary").count() == 1


def test_isolation_previous_test_was_rolled_back(db):
    assert db.query(User).filter_by(username="canary").count() == 0