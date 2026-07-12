import logging
import secrets
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import REFRESH_TOKEN
from data.team_registry import TRACKED_TEAMS, is_tracked
from ingest import (
    has_live_data,
    ingest_generated_docs,
    ingest_static_docs,
    refresh_live_data,
)
from main import ask_igla

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
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
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Wire the limiter into the app and register the 429 handler.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SituationRequest(BaseModel):
    situation: str
    team_id: int

    @field_validator("team_id")
    @classmethod
    def team_must_be_tracked(cls, value: int) -> int:
        """Reject ids IGLA holds no intel for.

        Without this an untracked id degrades silently: retrieval finds
        only the general shelf, best_distance is high, and the scope-gate
        rejects the query with a message about it not being a Valorant
        question. The user is told their question was bad when the real
        fault was the team id.
        """
        if not is_tracked(value):
            raise ValueError(f"team_id {value} is not a tracked team")
        return value


@app.post("/ask")
@limiter.limit("10/minute")
def ask_endpoint(request: Request, situation_request: SituationRequest):
    response = ask_igla(situation_request.situation, situation_request.team_id)
    return {"response": response}


@app.post("/refresh", status_code=202)
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


@app.get("/teams")
def list_teams():
    """Team picker source, derived from the registry SSOT."""
    return {"teams": TRACKED_TEAMS}


@app.get("/health")
def health():
    return {"status": "ok"}