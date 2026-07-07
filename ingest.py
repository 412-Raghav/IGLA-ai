import json
import logging
from pathlib import Path

from data.tactical_docs import TACTICAL_DOCUMENTS
from data.team_registry import team_ids
from data.vlr_live import fetch_team_tendencies
from rag.embedder import get_or_create_collection

logger = logging.getLogger("igla")
GENERATED_DOCS_DIR = Path(__file__).parent / "data" / "generated"

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


def _validate_static_docs():
    """Fail fast if any static doc lacks its scoping key.

    Every static doc must carry exactly one of team_id (team-specific) or
    scope (the general shelf). Neither field = silently unretrievable under
    any team filter; both = muddies the team/general split. Enforced here so
    the invariant is structural, not convention.
    """
    for doc in TACTICAL_DOCUMENTS:
        md = doc["metadata"]
        has_team = "team_id" in md
        has_scope = "scope" in md
        if has_team == has_scope:
            raise ValueError(
                f"{doc['id']}: metadata needs exactly one of team_id / scope"
            )


def ingest_static_docs():
    """Upsert the static tactical corpus.

    Cheap, idempotent, network-free — so the corpus is never empty even if
    every live scrape fails.
    """
    _validate_static_docs()
    collection = get_or_create_collection()
    _upsert_docs(collection, TACTICAL_DOCUMENTS, label="static tactical")


def _load_generated_doc(path):
    """Read one reviewed JSON strategy doc and shape it for upsert."""
    record = json.loads(path.read_text(encoding="utf-8"))
    return {
        "id": f"generated-team-{record['team_id']}",
        "text": record["doc_text"],
        "metadata": {
            "source": "generated",
            "team": record["team_name"],
            "team_tag": record["team_tag"],
            # int, matching 9b's team_id convention -- a stored string here
            # would silently never match the int filter (the 9b type trap).
            "team_id": int(record["team_id"]),
            "timespan": record["timespan"],
            "model": record["model"],
        },
    }


def ingest_generated_docs():
    """Upsert the human-reviewed generated strategy docs (one per team).

    Reads data/generated/*.json -- the reviewed, committed output of the
    operator-run generator. Network- and token-free (local file reads +
    local embedding), so it loads at boot alongside the static corpus.
    Deliberately does NOT import data/generate_docs.py: generation stays
    out of the serving path; ingestion only consumes its cached output.
    """
    collection = get_or_create_collection()
    paths = sorted(GENERATED_DOCS_DIR.glob("*.json"))
    docs = [_load_generated_doc(p) for p in paths]
    _upsert_docs(collection, docs, label="generated strategy")


def refresh_live_data():
    """Re-scrape vlr.gg tendencies for every tracked team and upsert them.

    Best-effort per team: a failed scrape is logged and skipped so one broken
    team never aborts the rest. This is what the scheduler runs on a cadence.
    """
    collection = get_or_create_collection()
    for team_id in team_ids():
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
    """True if the DB already holds live vlr.gg data from a previous run.

    Checks for the vlr.gg source specifically, not a total-count threshold:
    the static corpus AND the generated strategy docs both add to the count
    without being live data, so a count check would wrongly report live data
    present and skip the cold-start scrape.
    """
    collection = get_or_create_collection()
    result = collection.get(where={"source": "vlr.gg"}, limit=1)
    return bool(result["ids"])


def ingest_documents():
    """Full ingest: static corpus, generated strategy docs, then live refresh.

    For manual/local runs (python ingest.py). The deployed app calls
    ingest_static_docs() + ingest_generated_docs() and schedules
    refresh_live_data() instead.
    """
    ingest_static_docs()
    ingest_generated_docs()
    refresh_live_data()
    logger.info("Vector database is ready for queries.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ingest_documents()