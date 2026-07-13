"""Session service: create and validate server-side sessions.

Operations on the `sessions` table that turn it into working auth:
  create_session   -- mint an opaque session id at login, persist a row
                      with an expiry, return the id (rides in the cookie).
  validate_session -- look up a session id on each request; return the
                      User only if the session exists AND is unexpired.
  delete_session   -- logout: remove one session row (revocation).

Expiry is checked in Python against timezone-aware UTC (_utcnow in
models). An expired-but-still-present row is treated as invalid and
opportunistically deleted, so the table self-cleans on access.
"""

from datetime import timedelta

from sqlalchemy.orm import Session as DbSession

import config
from models import Session, User, _utcnow


def create_session(user_id: int, db: DbSession) -> str:
    """Create a session row for user_id and return its opaque id."""
    session = Session(
        user_id=user_id,
        expires_at=_utcnow() + timedelta(hours=config.SESSION_TTL_HOURS),
    )
    db.add(session)
    db.commit()
    return session.id


def validate_session(session_id: str, db: DbSession) -> User | None:
    """Return the User for a valid, unexpired session, else None.

    An expired session is deleted on access (self-cleaning) and treated
    as invalid.
    """
    session = db.get(Session, session_id)
    if session is None:
        return None

    if session.expires_at <= _utcnow():
        db.delete(session)
        db.commit()
        return None

    return session.user


def delete_session(session_id: str, db: DbSession) -> None:
    """Delete a session row (logout). No-op if it doesn't exist."""
    session = db.get(Session, session_id)
    if session is not None:
        db.delete(session)
        db.commit()