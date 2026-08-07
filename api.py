import logging
import secrets
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session as DbSession

import chat_service
from auth_routes import require_user, router as auth_router
from chat_routes import router as chat_router
from config import REFRESH_TOKEN, SCOPE_THRESHOLD
from data.team_registry import TRACKED_TEAMS, team_name, teams_mentioned
from db import get_db
from ingest import (
    has_live_data,
    ingest_generated_docs,
    ingest_static_docs,
    refresh_live_data,
)
from main import REJECTION_MESSAGE, ask_igla
from models import User
from rag.retriever import format_context, passes_scope_gate, retrieve_merged
from rate_limit import limiter
from upload_routes import router as upload_router

# The app owns root-logger config; library modules must not touch it. main.py
# used to call basicConfig at module scope and api.py imports main, so main's
# call ran first and silently no-opped this one -- basicConfig does nothing if
# handlers already exist. stream=sys.stdout never applied, which is why Railway
# tagged every log line severity:error despite the fix. The format string below
# was main.py's; it now belongs where it should have been all along.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("igla")


# Single-flight refresh: refresh_live_data rewrites the SHARED corpus, so two
# runs at once would double-write it. A refresh takes minutes; a rate limit
# bounds how OFTEN a trigger fires but not whether one is already running, so
# frequency and concurrency are separate guards. This flag, under a short-lived
# lock, is the concurrency half. Both the lifespan startup and the /refresh
# endpoint go through _trigger_refresh, so neither can stampede the other.
_refresh_lock = threading.Lock()
_refresh_in_progress = False


def _trigger_refresh() -> bool:
    """Start a refresh unless one is already running.

    Returns True if this call started the refresh, False if one was already in
    flight. The lock guards ONLY the flag read-and-set -- never the refresh
    itself, which runs on a daemon thread outside the critical section, so a
    minutes-long scrape never holds the lock. The worker clears the flag in a
    finally block, so a refresh that raises still frees the next trigger.
    """
    global _refresh_in_progress
    with _refresh_lock:
        if _refresh_in_progress:
            return False
        _refresh_in_progress = True

    def _worker():
        global _refresh_in_progress
        try:
            refresh_live_data()
        finally:
            with _refresh_lock:
                _refresh_in_progress = False

    threading.Thread(target=_worker, daemon=True).start()
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    ingest_static_docs()
    ingest_generated_docs()

    if not has_live_data():
        logger.info("No live data on startup; running an immediate refresh.")
        _trigger_refresh()

    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(upload_router)


# Wire the limiter into the app and register the 429 handler.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class AskRequest(BaseModel):
    conversation_id: UUID
    message: str

    @field_validator("message")
    @classmethod
    def message_nonempty(cls, value: str) -> str:
        """Reject a blank question at the boundary.

        team_id used to be validated here. It moved to NewConversation in
        chat_routes: the thread carries the anchor now, so an untracked id is
        caught once at thread birth instead of on every turn.
        """
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message must not be empty")
        return cleaned


@app.post(
    "/ask",
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "Conversation not found"},
        502: {"description": "Generation failed upstream"},
        503: {"description": "Retrieval unavailable"},
    },
)
@limiter.limit("10/minute")
def ask_endpoint(
    request: Request,
    ask_request: AskRequest,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    """One turn in a conversation: retrieve, gate, persist, generate.

    Orchestration lives here rather than in ask_igla because every
    intermediate value has to be persisted -- doc ids, best_distance, and the
    gate verdict. A function that fused all four steps could only return a
    string, and a string cannot report which of them happened.
    """
    conversation = chat_service.get_conversation(
        ask_request.conversation_id, user.id, db
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # MOVE re-scope: the anchor is whatever team the last user turn resolved
    # to -- get_current_anchor derives it from the prior turn's entity_scope,
    # seeded by the birth team. This turn overrides it ONLY when the message
    # names exactly one tracked team; zero or several named holds the current
    # anchor rather than swinging to an arbitrary one (a comparison is not a
    # switch). entity_scope stores the RESULT, so it becomes the anchor the
    # next turn derives from -- same {"team_ids": [id]} shape, no migration.
    current_anchor = chat_service.get_current_anchor(
        conversation.id, conversation.team_id, db
    )
    named = teams_mentioned(ask_request.message)
    effective_team_id = next(iter(named)) if len(named) == 1 else current_anchor

    entity_scope = {"team_ids": [effective_team_id]}
    anchor_name = team_name(effective_team_id)

    # Both retrieval attempts share one error boundary: a Chroma failure on
    # either maps to 503, and only retrieve_merged can throw inside it (the
    # gate check and record-building can't). anchor_name is resolved above,
    # OUTSIDE the boundary, so an unknown-team bug surfaces as 500 -- not a
    # masked 503 that would blame the vector store for a registry mismatch.
    try:
        # Attempt 1: the message as typed. Uploads join here, not only on the
        # retry -- best_distance must see them at the FIRST gate check, or a
        # note that would have rescued the turn never gets a vote.
        doc_ids, documents, best_distance, origins = retrieve_merged(
            ask_request.message, effective_team_id, user.id
        )
        gate = "pass" if passes_scope_gate(best_distance) else "reject"
        retrieval_record = [
            {
                "attempt": 1,
                "query": ask_request.message,
                "rewrite_of": None,
                "injected": None,
                "doc_ids": doc_ids,
                "origins": origins,
                "best_distance": best_distance,
                "threshold": SCOPE_THRESHOLD,
                "gate": gate,
            }
        ]

        # Attempt 2, only on reject: inject the thread anchor so a context-bound
        # follow-up ("why does that work?") clears the relevance gate. Measured
        # ~0.21 drop in PRX scope. Plain prefix, no pronoun resolution -- the
        # gate showed substitution buys nothing over concatenation, and "that"
        # has no team antecedent to substitute anyway.
        if gate == "reject":
            rewritten = f"{anchor_name} {ask_request.message}"
            logger.info(
                "Attempt 1 rejected (best_distance=%s); retrying with anchor "
                "rewrite (conversation_id=%s)",
                best_distance,
                conversation.id,
            )
            doc_ids, documents, best_distance, origins = retrieve_merged(
                rewritten, effective_team_id, user.id
            )
            gate = "pass" if passes_scope_gate(best_distance) else "reject"
            retrieval_record.append(
                {
                    "attempt": 2,
                    "query": rewritten,
                    "rewrite_of": ask_request.message,
                    "injected": {"team_name": anchor_name},
                    "doc_ids": doc_ids,
                    "origins": origins,
                    "best_distance": best_distance,
                    "threshold": SCOPE_THRESHOLD,
                    "gate": gate,
                }
            )
    except Exception:
        logger.exception("Retrieval failed (conversation_id=%s)", conversation.id)
        raise HTTPException(
            status_code=503, detail="Could not retrieve tactical intel"
        )

    # Read history BEFORE persisting this turn, so the current question cannot
    # appear in its own context.
    history = chat_service.get_history(conversation.id, db)
    logger.info(
        "Assembled history (conversation_id=%s, replayed_turns=%s)",
        conversation.id,
        len(history),
    )

    # One write, after retrieval, before Claude: a failed generation still
    # leaves the turn and its retrieval record auditable.
    chat_service.add_message(
        conversation.id,
        "user",
        ask_request.message,
        db,
        entity_scope=entity_scope,
        retrieval=retrieval_record,
    )
    chat_service.set_title_if_absent(conversation.id, ask_request.message, db)

    if gate == "reject":
        logger.info(
            "Query rejected after anchor rewrite (conversation_id=%s, best_distance=%s)",
            conversation.id,
            best_distance,
        )
        chat_service.add_message(
            conversation.id, "assistant", REJECTION_MESSAGE, db
        )
        return {
            "response": REJECTION_MESSAGE,
            "conversation_id": str(conversation.id),
            "gate": gate,
            "origins": [],
            "team_id": effective_team_id,
        }

    try:
        answer = ask_igla(
            ask_request.message, format_context(documents), history
        )
    except anthropic.APIError:
        logger.exception("Generation failed (conversation_id=%s)", conversation.id)
        raise HTTPException(status_code=502, detail="Upstream model unavailable")

    chat_service.add_message(conversation.id, "assistant", answer, db)
    return {
        "response": answer,
        "conversation_id": str(conversation.id),
        "gate": gate,
        "origins": origins,
        "team_id": effective_team_id,
    }


@app.post(
    "/refresh",
    status_code=202,
    responses={
        401: {"description": "Missing or invalid refresh token"},
        409: {"description": "A refresh is already running"},
        429: {"description": "Too many refresh requests"},
    },
)
@limiter.limit("3/minute")
def refresh_endpoint(request: Request, x_refresh_token: str | None = Header(None)):
    if (
        not REFRESH_TOKEN
        or not x_refresh_token
        or not secrets.compare_digest(x_refresh_token, REFRESH_TOKEN)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not _trigger_refresh():
        logger.info("Refresh requested but one is already running; returning 409.")
        raise HTTPException(status_code=409, detail="A refresh is already running")

    logger.info("Authorized refresh triggered; starting in background.")
    return {"status": "refresh started"}


INDEX_HTML = Path(__file__).parent / "index.html"


@app.get("/")
def serve_index():
    """Serve the single-page frontend, same-origin with the API.

    Serving from FastAPI (not opening from disk) puts the page and /ask on
    one origin -- which is what lets the session cookie attach. Path is
    anchored to this file's location, not the process CWD, so it resolves no
    matter where uvicorn is launched from.
    """
    return FileResponse(INDEX_HTML)


@app.get("/teams")
def list_teams():
    """Team picker source, derived from the registry SSOT."""
    return {"teams": TRACKED_TEAMS}


@app.get("/health")
def health():
    return {"status": "ok"}