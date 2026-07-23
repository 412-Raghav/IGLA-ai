"""Upload routes: user-provided opponent intel.

Grouped as an APIRouter and included into the app in api.py, mirroring
chat_routes. Guarded by require_user from line one -- an uploaded note belongs
to exactly one account.

Isolation is structural, not filtered: ingest writes to the caller's own
ChromaDB collection, selected by their server-assigned id. No where-clause
mediates access, so there is no clause to get wrong.

Every rejection is decided here, at the boundary, with its own status code.
Form fields do NOT flow through a Pydantic field_validator the way
NewConversation's team_id does -- so the tracked-team guard is written by
hand. Omit it and it silently does not exist.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from auth_routes import require_user
from data.team_registry import is_tracked
from models import User
from rag.uploads import ingest_upload

logger = logging.getLogger("igla")

router = APIRouter(prefix="/uploads", tags=["uploads"])

# Ingest is synchronous and CPU-bound: 256 KB is ~320 chunks, roughly ten
# seconds of local embedding. The route is a sync def, so FastAPI runs it in a
# threadpool and the event loop stays free -- but the request still holds for
# that long. Backgrounding the ingest is the documented next step.
MAX_UPLOAD_BYTES = 256 * 1024
ALLOWED_SUFFIXES = {".txt", ".md"}
MAX_FILENAME_CHARS = 200

UPLOAD_RESPONSES = {
    400: {"description": "Unreadable or empty note"},
    401: {"description": "Not authenticated"},
    413: {"description": "Note too large"},
    415: {"description": "Unsupported file type"},
    422: {"description": "Untracked team_id"},
}


@router.post("", status_code=201, responses=UPLOAD_RESPONSES)
def upload_intel_endpoint(
    file: UploadFile = File(...),
    team_id: int = Form(...),
    user: User = Depends(require_user),
):
    """Ingest one scouting note into the caller's private collection.

    Checks run cheapest-first: extension and team_id are decided before a byte
    is read, so a rejected upload never pays for the read or the embed.

    Args:
        file: A .txt or .md note, UTF-8 encoded.
        team_id: The opponent the note is about. Must be tracked.
        user: Injected by the guard; selects the collection.

    Returns:
        {"upload_id": str, "chunks": int, "source": str, "team_id": int}
    """
    original = Path(file.filename or "").name

    if Path(original).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415, detail="Only .txt and .md notes are supported"
        )

    if not is_tracked(team_id):
        raise HTTPException(
            status_code=422, detail=f"team_id {team_id} is not a tracked team"
        )

    # Read one byte past the cap: if it comes back over, the file is too big.
    # Content-Length is client-supplied and can lie or be absent, so the limit
    # is enforced on what was actually read, not on what was declared.
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Note exceeds the {MAX_UPLOAD_BYTES // 1024} KB limit",
        )

    try:
        # utf-8-sig, not utf-8: a note saved from Notepad carries a BOM, which
        # decodes clean under plain utf-8 but leaves \ufeff glued to the first
        # word -- silently embedded into chunk 0.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="Note must be UTF-8 encoded text"
        )

    # Truncated for storage only; the suffix check above ran on the full name,
    # so a very long filename cannot lose its extension and 415 by accident.
    # This is display metadata and is never used as a filesystem path.
    summary = ingest_upload(user.id, team_id, original[:MAX_FILENAME_CHARS], text)

    if summary["chunks"] == 0:
        raise HTTPException(
            status_code=400, detail="Note contains no readable text"
        )

    logger.info(
        "Ingested upload id=%s user_id=%s team_id=%s chunks=%s source=%s",
        summary["upload_id"],
        user.id,
        team_id,
        summary["chunks"],
        original,
    )
    return {**summary, "team_id": team_id}