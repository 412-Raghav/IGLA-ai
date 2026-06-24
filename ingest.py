import logging

from data.tactical_docs import TACTICAL_DOCUMENTS
from data.vlr_live import fetch_team_tendencies
from rag.embedder import get_or_create_collection

logger = logging.getLogger("igla")

# Teams to pull live tendencies for. Look up new IDs via vlr.search,
# never guess them. 624 = Paper Rex (verified).
TEAMS = [624]


def _upsert_docs(collection, docs, label):
    """Upsert a batch of {id, text, metadata} docs. Idempotent by id."""
    if not docs:
        logger.warning("No %s documents to ingest.", label)
        return
    collection.upsert(
        ids=[d["id"] for d in docs],
        documents=[d["text"] for d in docs],
        metadatas=[d["metadata"] for d in docs],
    )
    logger.info("Ingested %d %s documents.", len(docs), label)


def ingest_static_docs():
    """Upsert the static tactical corpus.

    Cheap, idempotent, network-free — so the corpus is never empty even if
    every live scrape fails.
    """
    collection = get_or_create_collection()
    _upsert_docs(collection, TACTICAL_DOCUMENTS, label="static tactical")


def refresh_live_data():
    """Re-scrape vlr.gg tendencies for every tracked team and upsert them.

    Best-effort per team: a failed scrape is logged and skipped so one broken
    team never aborts the rest. This is what the scheduler runs on a cadence.
    """
    collection = get_or_create_collection()
    for team_id in TEAMS:
        try:
            live_docs = fetch_team_tendencies(team_id)
        except Exception:
            logger.exception(
                "Live vlr.gg fetch failed for team_id=%s; skipping.", team_id
            )
            continue
        _upsert_docs(collection, live_docs, label=f"vlr.gg team {team_id}")
    logger.info("Live tendency refresh complete.")


def has_live_data():
    """True if the DB already holds docs beyond the static corpus.

    Lets the app skip a cold-start scrape when the volume already carries
    live data from a previous run.
    """
    collection = get_or_create_collection()
    return collection.count() > len(TACTICAL_DOCUMENTS)


def ingest_documents():
    """Full ingest: static corpus, then a live refresh.

    For manual/local runs (python ingest.py). The deployed app calls
    ingest_static_docs() and schedules refresh_live_data() instead.
    """
    ingest_static_docs()
    refresh_live_data()
    logger.info("Vector database is ready for queries.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ingest_documents()