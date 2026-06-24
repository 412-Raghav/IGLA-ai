import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from pydantic import BaseModel

from config import REFRESH_INTERVAL_HOURS
from ingest import has_live_data, ingest_static_docs, refresh_live_data
from main import ask_igla

# Ingestion now runs inside this process (not via ingest.py's __main__), so
# configure logging here too or its INFO logs won't reach the Railway logs.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("igla")

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: guarantee the static corpus, then move the live refresh onto a
    # schedule instead of running it on every boot.
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

    # Cold-start safety: if the volume holds no live data (fresh or wiped),
    # fetch once now in the background so we don't serve static-only for hours.
    if not has_live_data():
        logger.info("No live data on startup; running an immediate refresh.")
        threading.Thread(target=refresh_live_data, daemon=True).start()

    yield

    # Shutdown: stop the scheduler without blocking on an in-flight scrape.
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)


class SituationRequest(BaseModel):
    situation: str


@app.post("/ask")
def ask_endpoint(request: SituationRequest):
    response = ask_igla(request.situation)
    return {"response": response}