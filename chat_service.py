"""Conversation service: chat threads and their turns.

Operations on the `conversations` and `messages` tables:
  create_conversation -- open a thread anchored to one team.
  list_conversations  -- the sidebar: this user's threads, newest first.
  get_conversation    -- fetch one thread, ONLY if this user owns it.
  delete_conversation -- remove a thread; messages cascade.
  add_message         -- persist one turn.
  get_current_anchor  -- the team this thread is currently scoped to.
  get_history         -- assemble the Anthropic messages array.
  set_title_if_absent -- name the thread from its first question.

OWNERSHIP IS ENFORCED HERE, in the WHERE clause -- not in the route. Both
get_conversation and delete_conversation give the "not found" answer for a
thread that exists but belongs to someone else; the route turns that into a
404, not a 403, because 403 would confirm the row exists.

GATE-REJECTED TURNS ARE PERSISTED BUT NEVER REPLAYED (design decision 2.6).
add_message stores them -- that record is the measurement Session 2 opens
with. get_history drops them: a dangling question with no real answer is
noise, and replaying "that isn't a Valorant question" into the array would
teach Claude by example that rejection is a normal response shape.

Transactions: this layer commits its own, matching session_service.
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DbSession

from models import Conversation, Message

TITLE_MAX_LENGTH = 120  # must match Conversation.title's String(120)


def create_conversation(user_id: int, team_id: int, db: DbSession) -> Conversation:
    """Open a thread anchored to team_id. Title is set on the first turn."""
    conversation = Conversation(user_id=user_id, team_id=team_id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(user_id: int, db: DbSession) -> list[Conversation]:
    """This user's threads, newest activity first -- the sidebar order."""
    return list(
        db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        ).scalars()
    )


def get_conversation(
    conversation_id: UUID, user_id: int, db: DbSession
) -> Conversation | None:
    """Return the thread only if user_id owns it, else None.

    None covers both "no such thread" and "not yours" deliberately: the
    caller cannot tell them apart, so it cannot leak the difference.
    """
    return db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    ).scalar_one_or_none()


def delete_conversation(conversation_id: UUID, user_id: int, db: DbSession) -> bool:
    """Delete a thread and its messages. False if absent or not theirs."""
    conversation = get_conversation(conversation_id, user_id, db)
    if conversation is None:
        return False
    db.delete(conversation)
    db.commit()
    return True


def add_message(
    conversation_id: UUID,
    role: str,
    content: str,
    db: DbSession,
    entity_scope: dict | None = None,
    retrieval: list | None = None,
) -> Message:
    """Persist one turn and bump the thread's updated_at.

    entity_scope and retrieval belong on USER rows -- they describe how the
    question was processed. Assistant rows carry only the resulting text.

    The updated_at bump lives here rather than at the call site so it cannot
    be forgotten: inserting a message does not touch the conversation row, so
    the model's onupdate never fires on its own. Its VALUE is not asserted in
    the suite -- now() is frozen at transaction start, and the fixture runs
    every test inside one transaction. The UPDATE is emitted regardless.
    """
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        entity_scope=entity_scope,
        retrieval=retrieval,
    )
    db.add(message)
    db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    db.commit()
    db.refresh(message)
    return message


def get_current_anchor(
    conversation_id: UUID, birth_team_id: int, db: DbSession
) -> int:
    """The team this thread is currently scoped to (the MOVE anchor).

    The anchor is whatever team the LAST user turn resolved to, recorded in
    that turn's entity_scope. Seeded by birth_team_id when the thread has no
    user turn yet. The last user row is read regardless of gate outcome: a
    turn that NAMED a team moved the anchor whether or not its retrieval
    passed, so a rejected turn still carries the scope forward.

    Ordering is (created_at, id) descending -- the id tiebreaker matters
    because Postgres now() is frozen per transaction, so created_at alone
    cannot order rows written together.
    """
    row = db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if row is None or not row.entity_scope:
        return birth_team_id
    team_ids = row.entity_scope.get("team_ids")
    if not team_ids:
        return birth_team_id
    return team_ids[0]


def _gate_passed(message: Message) -> bool:
    """True if this user turn's retrieval cleared the scope gate.

    Reads the LAST attempt, not the first: Session 2's rewrite-and-retry
    appends attempt 2 to the same list, and it is the final attempt that
    decides whether the turn was answered.
    """
    if not message.retrieval:
        return False
    return message.retrieval[-1].get("gate") == "pass"


def get_history(conversation_id: UUID, db: DbSession) -> list[dict]:
    """Assemble the Anthropic messages array for this thread.

    Returns only COMPLETE, gate-passing exchanges. Two kinds of turn drop out:
      - gate-rejected pairs (decision 2.6)
      - a user turn with no assistant reply, which is what a failed Claude
        call leaves behind

    Ordering is (created_at, id), and the id tiebreaker is load-bearing:
    Postgres now() returns TRANSACTION start time, so rows written inside one
    transaction share a created_at exactly. Ordering on created_at alone
    would be non-deterministic there.
    """
    rows = list(
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        ).scalars()
    )

    history: list[dict] = []
    index = 0
    while index < len(rows):
        turn = rows[index]
        if turn.role != "user":
            index += 1
            continue

        reply = rows[index + 1] if index + 1 < len(rows) else None
        if reply is None or reply.role != "assistant":
            index += 1
            continue

        if _gate_passed(turn):
            history.append({"role": "user", "content": turn.content})
            history.append({"role": "assistant", "content": reply.content})
        index += 2

    return history


def set_title_if_absent(
    conversation_id: UUID, first_message: str, db: DbSession
) -> None:
    """Name the thread from its first question. No-op if already named.

    Truncated to the column width: title is String(120), and an untruncated
    question would raise DataError on insert and 500 the request.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.title is not None:
        return
    conversation.title = first_message.strip()[:TITLE_MAX_LENGTH]
    db.commit()