import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import REFRESH_INTERVAL_HOURS
from ingest import has_live_data, ingest_static_docs, refresh_live_data
from main import ask_igla

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("igla")

# Rate limiter keyed on client IP. In-memory store: counts reset on restart
# and are per-replica — fine for a single replica; Redis is the multi-replica
# upgrade path.
limiter = Limiter(key_func=get_remote_address)

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ingest_static_docs()

    scheduler.add_job(
        refresh_live_data,
        trigger="interval",
        hours=REFRESH_INTERVAL_HOURS,
        id="vlr_refresh",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    if not has_live_data():
        logger.info("No live data on startup; running an immediate refresh.")
        threading.Thread(target=refresh_live_data, daemon=True).start()

    yield

    scheduler.shutdown(wait=False)


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


@app.get("/health")
def health():
    return {"status": "ok"}