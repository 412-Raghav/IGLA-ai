"""Conversation routes: thread lifecycle for multi-turn chat.

Grouped as an APIRouter and included into the app in api.py. Every route is
guarded by require_user -- a thread belongs to exactly one account.

404 NOT 403 for a thread the caller does not own. 403 would confirm the row
exists, telling an attacker they guessed a real conversation_id belonging to
someone else. The service layer enforces ownership in the WHERE clause and
returns None for both "absent" and "not yours"; this layer cannot tell them
apart, so it cannot leak the difference.

/ask is NOT here. It stays in api.py, where the slowapi limiter is wired and
requires `request: Request` first by name.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session as DbSession

import chat_service
from auth_routes import require_user
from data.team_registry import is_tracked
from db import get_db
from models import Conversation, Message, User

logger = logging.getLogger("igla")

router = APIRouter(prefix="/conversations", tags=["conversations"])

NOT_FOUND_RESPONSE = {404: {"description": "Conversation not found"}}


class NewConversation(BaseModel):
    team_id: int

    @field_validator("team_id")
    @classmethod
    def team_must_be_tracked(cls, value: int) -> int:
        """Reject ids IGLA holds no intel for.

        The same guard SituationRequest carries in api.py, moved to thread
        creation. team_id is now asked once, at thread birth, and every turn
        inherits it -- so an untracked id would poison an entire thread
        rather than one request.
        """
        if not is_tracked(value):
            raise ValueError(f"team_id {value} is not a tracked team")
        return value


def _serialize_conversation(conversation: Conversation) -> dict:
    """Thread summary -- the shape the sidebar renders."""
    return {
        "id": str(conversation.id),
        "team_id": conversation.team_id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _serialize_message(message: Message) -> dict:
    """One turn, as the frontend renders it.

    `gate` is exposed deliberately: null on assistant rows, and the
    scope-gate verdict on user rows. The frontend pairs them to style a
    rejected exchange distinctly -- which puts the Session 2 problem on
    screen instead of buried in Postgres.

    doc_ids and best_distance are NOT exposed. They are diagnostics, and
    the client has no use for them today.
    """
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
        "gate": message.retrieval[-1].get("gate") if message.retrieval else None,
    }


@router.post("", status_code=201)
def create_conversation_endpoint(
    new_conversation: NewConversation,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """Open a thread anchored to a team. Title is set on the first turn."""
    conversation = chat_service.create_conversation(
        user.id, new_conversation.team_id, db
    )
    logger.info(
        "Created conversation id=%s user_id=%s team_id=%s",
        conversation.id,
        user.id,
        conversation.team_id,
    )
    return _serialize_conversation(conversation)


@router.get("")
def list_conversations_endpoint(
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """This user's threads, newest activity first."""
    conversations = chat_service.list_conversations(user.id, db)
    return {"conversations": [_serialize_conversation(c) for c in conversations]}


@router.get("/{conversation_id}", responses=NOT_FOUND_RESPONSE)
def get_conversation_endpoint(
    conversation_id: UUID,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """One thread with its turns. 404 if absent OR not yours."""
    conversation = chat_service.get_conversation(conversation_id, user.id, db)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    payload = _serialize_conversation(conversation)
    payload["current_team_id"] = chat_service.get_current_anchor(
        conversation.id, conversation.team_id, db
    )
    payload["messages"] = [_serialize_message(m) for m in conversation.messages]
    return payload


@router.delete(
    "/{conversation_id}", status_code=204, responses=NOT_FOUND_RESPONSE
)
def delete_conversation_endpoint(
    conversation_id: UUID,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """Delete a thread and its turns. 404 if absent OR not yours."""
    if not chat_service.delete_conversation(conversation_id, user.id, db):
        raise HTTPException(status_code=404, detail="Conversation not found")