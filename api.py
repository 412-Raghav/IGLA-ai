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
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session as DbSession

import chat_service
from auth_routes import require_user, router as auth_router
from chat_routes import router as chat_router
from config import REFRESH_TOKEN
from data.team_registry import TRACKED_TEAMS
from db import get_db
from ingest import (
    has_live_data,
    ingest_generated_docs,
    ingest_static_docs,
    refresh_live_data,
)
from main import REJECTION_MESSAGE, ask_igla
from models import User
from rag.retriever import format_context, passes_scope_gate, retrieve_ranked

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

# Rate limiter keyed on client IP. In-memory store: counts reset on restart
# and are per-replica — fine for a single replica; Redis is the multi-replica
# upgrade path.
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ingest_static_docs()
    ingest_generated_docs()

    if not has_live_data():
        logger.info("No live data on startup; running an immediate refresh.")
        threading.Thread(target=refresh_live_data, daemon=True).start()

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

    # Session 1: no entity extraction, so scope is the thread anchor alone.
    # Session 2 adds names/maps pulled from the message; the column already
    # holds the right shape, so that lands without a migration.
    entity_scope = {"team_ids": [conversation.team_id]}

    try:
        doc_ids, documents, best_distance = retrieve_ranked(
            ask_request.message, team_id=conversation.team_id
        )
    except Exception:
        logger.exception("Retrieval failed (conversation_id=%s)", conversation.id)
        raise HTTPException(
            status_code=503, detail="Could not retrieve tactical intel"
        )

    gate = "pass" if passes_scope_gate(best_distance) else "reject"

    # A list from day one, length 1 today. Session 2's rewrite-and-retry
    # appends attempt 2 rather than needing a schema change.
    retrieval_record = [
        {
            "attempt": 1,
            "query": ask_request.message,
            "doc_ids": doc_ids,
            "best_distance": best_distance,
            "gate": gate,
        }
    ]

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
            "Query rejected by scope-gate (conversation_id=%s, best_distance=%s)",
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
    }


@app.post(
    "/refresh",
    status_code=202,
    responses={401: {"description": "Missing or invalid refresh token"}},
)
def refresh_endpoint(x_refresh_token: str | None = Header(None)):
    if (
        not REFRESH_TOKEN
        or not x_refresh_token
        or not secrets.compare_digest(x_refresh_token, REFRESH_TOKEN)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info("Authorized refresh triggered; starting in background.")
    threading.Thread(target=refresh_live_data, daemon=True).start()
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