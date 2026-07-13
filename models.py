"""SQLAlchemy ORM models for IGLA's auth datastore.

Two tables:
  users    -- one row per account. Stores a bcrypt password HASH only;
              the plaintext password is never persisted (hashing lands
              in the auth layer, STEP 4).
  sessions -- one row per active login. A server-side session store is
              what makes server-side-sessions (vs JWT) legible: logout
              and "revoke everywhere" are a DELETE on this table, not a
              token-denylist bolt-on.

Both inherit Base from db.py so Alembic sees them for autogeneration.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC now, for Python-side session expiry math."""
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        String(43), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sessions")