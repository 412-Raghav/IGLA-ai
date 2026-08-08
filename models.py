"""SQLAlchemy ORM models for IGLA's auth datastore and chat history.

Four tables:
  users         -- one row per account. Stores a bcrypt password HASH only;
                   the plaintext password is never persisted (hashing lands
                   in the auth layer, STEP 4).
  sessions      -- one row per active login. A server-side session store is
                   what makes server-side-sessions (vs JWT) legible: logout
                   and "revoke everywhere" are a DELETE on this table, not a
                   token-denylist bolt-on.
  conversations -- one row per chat thread. team_id is the thread ANCHOR:
                   the dropdown pick at creation, immutable, and the scope
                   every turn falls back to when a follow-up names no team.
  messages      -- one row per turn. entity_scope records what the turn was
                   scoped to; retrieval records what came back and whether
                   the gate passed. Both sit on the USER row -- they describe
                   the processing of that message, not the answer.

ChromaDB stores intel VECTORS, not conversations. History needs a relational
store; these two tables are it.

All inherit Base from db.py so Alembic sees them for autogeneration.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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

    conversations: Mapped[list["Conversation"]] = relationship(
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


class Conversation(Base):
    """One chat thread. Owned by exactly one user, anchored to one team."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="(Message.created_at, Message.id)",
    )


class Message(Base):
    """One turn. Gate-rejected turns are persisted -- that IS the measurement --
    but excluded from replay. See get_history in chat_service.
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    entity_scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retrieval: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class TeamInstruction(Base):
    """One analyst's standing instruction for one opponent.

    Keyed (user_id, team_id) with a UNIQUE constraint: a team instruction is a
    current-state object, not an event log, so there is exactly one live row
    per (user, team) and a second write REPLACES it (upsert) rather than
    appending. The uniqueness is enforced here, in the schema -- the every-turn
    read is then a point lookup with no "which row is latest" ambiguity, and
    the frozen-now() tiebreaker that get_current_anchor/get_history carry does
    not arise. See docs/phase-10-instructions-design.md.

    instructions_text is String(2000); the API rejects over-length input rather
    than truncating (an instruction is load-bearing, unlike a conversation
    title). MAX_INSTRUCTION_CHARS in instruction_service mirrors this width.
    """

    __tablename__ = "team_instructions"
    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_team_instructions_user_team"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(nullable=False)
    instructions_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )