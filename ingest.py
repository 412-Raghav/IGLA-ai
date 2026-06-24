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


def ingest_documents():
    """Populate ChromaDB with tactical documents.

    Static docs always ingest, so the corpus is never empty. Live vlr.gg
    docs are best-effort per team: a failed fetch is logged and skipped so
    the app still boots. Upsert-by-id makes re-runs idempotent.
    """
    collection = get_or_create_collection()

    # 1. Static corpus — always present, never depends on the network.
    _upsert_docs(collection, TACTICAL_DOCUMENTS, label="static tactical")

    # 2. Live vlr.gg tendencies — best-effort, must never block startup.
    for team_id in TEAMS:
        try:
            live_docs = fetch_team_tendencies(team_id)
        except Exception:
            logger.exception("Live vlr.gg fetch failed for team_id=%s; skipping.", team_id)
            continue
        _upsert_docs(collection, live_docs, label=f"vlr.gg team {team_id}")

    logger.info("Vector database is ready for queries.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ingest_documents()