"""Instruction routes: a per-team standing instruction, injected every turn.

Grouped as an APIRouter and included into the app in api.py, mirroring
chat_routes and upload_routes. Guarded by require_user from line one -- an
instruction belongs to exactly one account, keyed (user_id, team_id) with
user_id taken from the session, never the request.

The tracked-team guard is written by hand against the path parameter, the same
is_tracked check chat_routes runs in a Pydantic validator and upload_routes
runs inline: an instruction for a team IGLA holds no intel for is dead data, so
it is refused at the boundary rather than stored.

PUT sets a real instruction and DELETE clears one -- two distinct verbs for two
distinct intents, so a blank PUT is a 422, not a way to store "". The service
keeps exactly one representation of 'no instruction' (an absent row); the
routes never introduce a second.

Length is enforced HERE, after strip, and REJECTED past the cap rather than
truncated -- an instruction is load-bearing, unlike a conversation title, so
silently clipping it could drop the decisive line. The cap constant lives with
the service (MAX_INSTRUCTION_CHARS), imported so there is one source.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

import instruction_service
from auth_routes import require_user
from data.team_registry import is_tracked
from db import get_db
from models import User

logger = logging.getLogger("igla")

router = APIRouter(prefix="/instructions", tags=["instructions"])

INSTRUCTION_RESPONSES = {
    401: {"description": "Not authenticated"},
    422: {"description": "Untracked team_id, or instruction blank or too long"},
}
NOT_FOUND_RESPONSE = {404: {"description": "No instruction set for this team"}}


class InstructionBody(BaseModel):
    instructions_text: str


def _require_tracked(team_id: int) -> None:
    """Reject a team_id IGLA holds no intel for. Raises 422, else returns.

    The same guard chat_routes and upload_routes enforce, here against the path
    parameter. A path int is well-formed but not necessarily meaningful; an
    untracked id can never anchor a thread, so an instruction for it is refused
    at the boundary.
    """
    if not is_tracked(team_id):
        raise HTTPException(
            status_code=422, detail=f"team_id {team_id} is not a tracked team"
        )


@router.get("/{team_id}", responses=INSTRUCTION_RESPONSES)
def get_instruction_endpoint(
    team_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """The caller's instruction for one team, or an empty string if unset.

    Empty string, not 404: an unset instruction is a normal state the frontend
    binds straight into a textarea, not an error. The (user_id, team_id) key
    means this only ever reads the caller's own row.
    """
    _require_tracked(team_id)
    text = instruction_service.get_instruction(user.id, team_id, db)
    return {"instructions_text": text or ""}


@router.put("/{team_id}", responses=INSTRUCTION_RESPONSES)
def put_instruction_endpoint(
    team_id: int,
    body: InstructionBody,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """Set (create or replace) the caller's instruction for one team.

    Checks run cheapest-first: tracked-team, then strip, then length. Length is
    measured AFTER strip, so trailing whitespace from a paste never trips a
    false rejection; a real over-cap instruction is refused (422), not
    truncated. A blank instruction is refused too -- DELETE is how you clear
    one. Returns the stored text so the client renders exactly what persisted.
    """
    _require_tracked(team_id)

    text = body.instructions_text.strip()
    if not text:
        raise HTTPException(
            status_code=422, detail="Instruction must not be blank"
        )
    if len(text) > instruction_service.MAX_INSTRUCTION_CHARS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Instruction exceeds "
                f"{instruction_service.MAX_INSTRUCTION_CHARS} characters"
            ),
        )

    instruction_service.upsert_instruction(user.id, team_id, text, db)
    logger.info(
        "Set instruction user_id=%s team_id=%s chars=%s",
        user.id,
        team_id,
        len(text),
    )
    return {"instructions_text": text}


@router.delete(
    "/{team_id}",
    status_code=204,
    responses={**INSTRUCTION_RESPONSES, **NOT_FOUND_RESPONSE},
)
def delete_instruction_endpoint(
    team_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """Clear the caller's instruction for one team. 404 if none was set.

    404-not-204 on an absent instruction mirrors delete_conversation and
    delete_upload: the service's bool return distinguishes 'removed a row' from
    'there was nothing to remove', so the route can answer honestly rather than
    reporting a no-op as success.
    """
    _require_tracked(team_id)
    if not instruction_service.delete_instruction(user.id, team_id, db):
        raise HTTPException(
            status_code=404, detail="No instruction set for this team"
        )