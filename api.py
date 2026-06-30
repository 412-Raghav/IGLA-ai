import logging
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import REFRESH_TOKEN
from ingest import has_live_data, ingest_static_docs, refresh_live_data
from main import ask_igla

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("igla")

# Rate limiter keyed on client IP. In-memory store: counts reset on restart
# and are per-replica — fine for a single replica; Redis is the multi-replica
# upgrade path.
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ingest_static_docs()

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


@app.post("/ask")
@limiter.limit("10/minute")
def ask_endpoint(request: Request, situation_request: SituationRequest):
    response = ask_igla(situation_request.situation)
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


@app.get("/health")
def health():
    return {"status": "ok"}