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
from rag.uploads import get_user_collection, ingest_upload

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


@router.get("", responses={401: {"description": "Not authenticated"}})
def list_uploads_endpoint(user: User = Depends(require_user)):
    """List the caller's uploaded notes, grouped by note, newest first.

    One uploaded note becomes several chunks sharing an upload_id. This scans
    the caller's own collection metadata and folds those chunks back into one
    summary per note. The chunks ARE the registry, so there is no separate
    table to keep in sync -- delete a note's chunks and it is gone from this
    list with nothing left to reconcile.

    Isolation is structural: only get_user_collection(user.id) is opened, so a
    caller can only ever see their own uploads. There is no user_id filter to
    forget, and no other user's collection is reachable from here.

    Note the shape of what this can show -- upload_id, source, team_id,
    uploaded_at, chunk count. All of it lives on the chunks. Anything that does
    NOT (a file's byte size, an ingest status, a soft-delete audit trail) has
    no home under this design; that absence is the deliberate trigger to add a
    Postgres uploads table if such a need ever lands.

    Returns:
        A list of {"upload_id", "source", "team_id", "uploaded_at", "chunks"},
        sorted newest first. Empty list when the user has never uploaded, or
        has deleted every note (the collection persists but scans empty).
    """
    collection = get_user_collection(user.id)
    if collection is None:
        return []

    result = collection.get(include=["metadatas"])

    notes: dict[str, dict] = {}
    for meta in result["metadatas"] or []:
        upload_id = meta["upload_id"]
        if upload_id not in notes:
            notes[upload_id] = {
                "upload_id": upload_id,
                "source": meta["source"],
                "team_id": meta["team_id"],
                "uploaded_at": meta["uploaded_at"],
                "chunks": 0,
            }
        notes[upload_id]["chunks"] += 1

    # uploaded_at is an ISO-8601 UTC string, so a lexicographic sort IS
    # chronological -- no datetime parsing needed to order newest-first.
    return sorted(
        notes.values(), key=lambda note: note["uploaded_at"], reverse=True
    )


@router.delete(
    "/{upload_id}",
    status_code=204,
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "No such upload"},
    },
)
def delete_upload_endpoint(upload_id: str, user: User = Depends(require_user)):
    """Delete one of the caller's uploaded notes and all its chunks.

    Isolation is structural: only the caller's own collection is opened, so a
    caller can only ever delete their own note. Another user's collection is
    never named, so there is no cross-user path from here.

    "Not yours" and "doesn't exist" deliberately collapse to one 404. A caller
    with no uploads has no collection (get_user_collection returns None); a
    caller whose collection holds no chunk with this upload_id gets the same
    answer. Returning 403 for a note owned by someone else would confirm the
    note exists -- an enumeration signal. 404 leaks nothing.

    ChromaDB's delete(where=...) is a silent no-op when nothing matches: it
    neither raises nor reports how many rows went. So existence is checked
    first, and that same read supplies the chunk count for the log. Deleting
    then inspecting would leave no way to tell "removed a note" from "removed
    nothing."
    """
    collection = get_user_collection(user.id)
    if collection is None:
        raise HTTPException(status_code=404, detail="No such upload")

    # Only ["ids"] is read; the where-filter matches this note's chunks.
    matching = collection.get(where={"upload_id": upload_id})
    chunk_ids = matching["ids"]
    if not chunk_ids:
        raise HTTPException(status_code=404, detail="No such upload")

    collection.delete(where={"upload_id": upload_id})
    logger.info(
        "Deleted upload id=%s user_id=%s chunks=%s",
        upload_id,
        user.id,
        len(chunk_ids),
    )