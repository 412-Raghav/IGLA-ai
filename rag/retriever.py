"""Retrieval and ranking for IGLA.

Four entry points; which one to call depends on who is asking.

    retrieve_merged   -- the serving path. Shared corpus plus the caller's
                         own uploads, ranked together. Needs a user_id.
    retrieve_ranked   -- one collection. What the eval harness scores and
                         what retrieve_context wraps. No user, no uploads.
    retrieve_context  -- retrieve_ranked plus formatting, for callers that
                         want a prompt block and nothing else.
    format_context    -- formatting alone, for callers already holding
                         documents.

_retrieve_candidates is the shared layer under the first two. It exists so a
merge can rank two result sets together: parallel lists can only be
recombined by index, and indices are not comparable across collections.

KNOWN GAP: the eval harness scores retrieve_ranked, so it measures
shared-corpus ranking only. A user with uploads is served a ranking the eval
cannot see.
"""


from config import SCOPE_THRESHOLD
from rag.embedder import get_or_create_collection
from rag.uploads import PROVENANCE_UPLOAD, get_user_collection


# When a query is scoped to a team, pull a deeper candidate pool than the
# caller asked for, so the team's own docs — which general-shelf docs can
# out-rank on keyword-dense queries — are in hand to promote. Sized above the
# largest per-team pool (~12 for Paper Rex) so the re-rank sees the full
# scoped set, not a truncated view. Tunable; raise if pools grow.
_RERANK_POOL = 20
NO_CONTEXT = "No relevant tactical context found"


def passes_scope_gate(
    best_distance: float | None, threshold: float = SCOPE_THRESHOLD
) -> bool:
    """The scope-gate decision, defined once for serving and for eval.

    Production rejects when retrieval returned nothing, or when the closest
    document sits farther than `threshold`. Both callers import this, so an
    eval cannot silently score a different rule than the one that serves.
    """
    if best_distance is None:
        return False
    return best_distance <= threshold


def _build_where(team_id):
    """Build a ChromaDB where-filter scoping retrieval to one team.

    Returns None when team_id is None, so the caller's default behavior
    (search the whole collection) is preserved byte-for-byte.

    When a team_id is given, the filter matches that team's docs OR the
    universal 'general' shelf, so team-agnostic theory (retake timing,
    agent combos) is always retrievable regardless of which team is
    being scouted.
    """
    if team_id is None:
        return None
    return {"$or": [{"team_id": team_id}, {"scope": "general"}]}


def _rank_key(record: dict) -> tuple[bool, float]:
    """Sort key placing the scouted team's docs above the general shelf.

    Returns (is_general, distance). False sorts before True, so team docs --
    and, on the serving path, the caller's own uploads, which carry no scope
    key at all -- rank ahead of the universal shelf, ordered by distance
    within each tier.

    This replaces the earlier stable sort on the boolean alone. That version
    relied on the incoming list already being distance-ascending, which is
    true of one Chroma result and false of two concatenated ones. Making
    distance an explicit part of the key is what lets one rule order a
    merged pool without changing what it does to a single one.
    """
    return (record["metadata"].get("scope") == "general", record["distance"])


def _origin(record: dict) -> str:
    """Label which shelf a record came from, for the response payload.

    Reads `provenance`, which only upload chunks carry. Deliberately NOT
    `source`: the shared corpus stores its origin there ("vlr.gg"), while an
    upload stores its filename there. The same key means two different
    things across the two schemas, so it cannot be the discriminator.
    """
    return (
        "upload"
        if record["metadata"].get("provenance") == PROVENANCE_UPLOAD
        else "corpus"
    )


def _retrieve_candidates(
    query: str, fetch_k: int, where: dict | None, collection
) -> list[dict]:
    """Query one collection and return per-document records.

    The shared layer under both retrieval entry points. Bundling each
    document with its own distance and metadata is what makes a two-source
    merge possible at all: parallel lists can only be recombined by index,
    and indices are not comparable across collections.

    The zip here is safe -- the four lists come from a single Chroma result
    and are equal-length by construction. It is pairing ACROSS results that
    breaks, since a query for n_results returns min(n_results, N).

    Returns [] when nothing matches; the caller decides what that means.
    """
    results = collection.query(
        query_texts=[query],
        n_results=fetch_k,
        where=where,
        include=["documents", "distances", "metadatas"],
    )
    return [
        {
            "id": doc_id,
            "document": document,
            "distance": distance,
            "metadata": metadata,
        }
        for doc_id, document, distance, metadata in zip(
            results["ids"][0],
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
        )
    ]


def retrieve_ranked(
    query: str, n_results: int = 3, team_id: int | None = None, collection=None
) -> tuple[list[str], list[str], float | None]:
    """Query the collection and apply the team-first re-rank.

    The single retrieval path. Returns (ids, documents, best_distance),
    trimmed to n_results, with best_distance the true closest match captured
    before the re-rank. retrieve_context formats the documents for the LLM;
    the eval reads the ids to score rank -- so the eval exercises the exact
    ranking production serves and cannot silently drift from it.

    Args:
        query: The tactical situation from the user.
        n_results: How many documents to return (default 3).
        team_id: If given, scope retrieval to that team's docs plus the
            universal 'general' shelf, then re-rank team docs first.
        collection: Injection seam. Defaults to the production collection;
            experiments pass an alternate so they score through this exact
            ranking path rather than a forked copy.

    Returns:
        (ids, documents, best_distance).
    """
    if collection is None:
        collection = get_or_create_collection()

    # Scoped queries fetch a deeper pool to re-rank from; unscoped (global)
    # queries keep the original behavior byte-for-byte.
    fetch_k = _RERANK_POOL if team_id is not None else n_results

    records = _retrieve_candidates(
        query, fetch_k, _build_where(team_id), collection
    )
    if not records:
        return [], [], None

    # Captured before the re-rank: the scope-gate must keep seeing the true
    # closest match, not the promoted team doc. Chroma returns records
    # distance-ascending, so record 0 is the closest.
    best_distance = records[0]["distance"]

    if team_id is not None:
        records = sorted(records, key=_rank_key)

    top = records[:n_results]
    return [r["id"] for r in top], [r["document"] for r in top], best_distance


def retrieve_merged(
    query: str, team_id: int, user_id: int, n_results: int = 3
) -> tuple[list[str], list[str], float | None, list[str]]:
    """Retrieve across the shared corpus and the caller's own uploads.

    Isolation is structural, not filtered: only this user's collection is
    ever opened, so another analyst's notes cannot surface no matter what a
    where-filter says. There is no isolation clause to get wrong and none to
    regress in a later refactor.

    Both sides are scoped to team_id. The upload filter is a bare exact
    match, not _build_where's $or -- upload chunks carry no `scope` key, so
    there is no general shelf on that side. Without it, a two-chunk
    collection's nearest neighbour (measured at 0.703, inside every
    threshold we run) would contaminate every thread about a different team:
    "nearest" carries almost no information when N is 2.

    Args:
        query: The tactical situation from the user.
        team_id: Thread anchor. Scopes both collections.
        user_id: Whose uploads to search. Server-assigned, never from input.
        n_results: How many documents to return (default 3).

    Returns:
        (ids, documents, best_distance, origins) -- origins parallel to ids,
        each "upload" or "corpus".
    """
    shared = _retrieve_candidates(
        query, _RERANK_POOL, _build_where(team_id), get_or_create_collection()
    )

    # Zero-uploads fast path: no collection, no second query, no embed.
    user_collection = get_user_collection(user_id)
    uploads = (
        _retrieve_candidates(
            query, n_results, {"team_id": team_id}, user_collection
        )
        if user_collection is not None
        else []
    )

    if not shared and not uploads:
        return [], [], None, []

    # min across both, not records[0]: each list is distance-ascending on its
    # own, the concatenation is not, and the gate must see the true closest
    # match wherever it came from. inf for a missing side -- 0.0 would pass
    # every query ever asked, and would never raise.
    best_distance = min(
        shared[0]["distance"] if shared else float("inf"),
        uploads[0]["distance"] if uploads else float("inf"),
    )

    # One rule orders the merged pool: _rank_key demotes the general shelf
    # and sorts by distance within each tier. Uploads carry no `scope`, so
    # they land in the same tier as team docs and compete on distance alone.
    merged = sorted(shared + uploads, key=_rank_key)[:n_results]
    return (
        [r["id"] for r in merged],
        [r["document"] for r in merged],
        best_distance,
        [_origin(r) for r in merged],
    )


def format_context(documents: list[str]) -> str:
    """Format retrieved documents into the block the LLM prompt carries.

    Extracted so the serving path can call retrieve_ranked directly -- it needs
    the ids for the per-turn retrieval record, which retrieve_context discards
    -- and still format identically. One formatter, two callers; a second copy
    would drift the first time either side was edited.
    """
    if not documents:
        return NO_CONTEXT
    return "\n\n".join(
        f"[Tactical Intel {i + 1}]:\n{doc}" for i, doc in enumerate(documents)
    )


def retrieve_context(
    query: str, n_results: int = 3, team_id: int | None = None
) -> tuple[str, float | None]:
    """Search the vector database for relevant tactical context.

    Returns a (context, best_distance) pair. best_distance is the cosine
    distance of the closest-matching document — smaller means more relevant.
    It is None when the collection returns nothing, so the caller can tell
    "empty database" apart from "a genuine but weak match."

    Args:
        query: The tactical situation from the user.
        n_results: How many documents to retrieve (default 3).
        team_id: If given, scope retrieval to that team's docs plus the
            universal 'general' shelf. If None (default), search everything.

    Returns:
        (formatted_context, best_distance).
    """
    _, documents, best_distance = retrieve_ranked(
        query, n_results=n_results, team_id=team_id
    )
    return format_context(documents), best_distance