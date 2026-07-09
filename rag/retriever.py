from rag.embedder import get_or_create_collection


# When a query is scoped to a team, pull a deeper candidate pool than the
# caller asked for, so the team's own docs — which general-shelf docs can
# out-rank on keyword-dense queries — are in hand to promote. Sized above the
# largest per-team pool (~12 for Paper Rex) so the re-rank sees the full
# scoped set, not a truncated view. Tunable; raise if pools grow.
_RERANK_POOL = 20


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


def _team_first_order(metadatas):
    """Stable index order placing the scouted team's docs above the general
    shelf. Returns indices (not docs) so ids, documents, and distances reorder
    by one permutation and stay aligned -- the eval scores by id while serving
    formats docs, so both must reorder identically.

    General docs (scope == "general") sort after team docs; semantic
    (distance-ascending) order is preserved within each group (stable sort).
    """
    return sorted(
        range(len(metadatas)),
        key=lambda i: metadatas[i].get("scope") == "general",
    )


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

    results = collection.query(
        query_texts=[query],
        n_results=fetch_k,
        where=_build_where(team_id),
        include=["documents", "distances", "metadatas"],
    )
    ids = results["ids"][0]
    documents = results["documents"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]

    if not documents:
        return [], [], None

    # Captured before the re-rank: the scope-gate must keep seeing the true
    # closest match, not the promoted team doc.
    best_distance = distances[0]

    if team_id is not None:
        order = _team_first_order(metadatas)
        ids = [ids[i] for i in order]
        documents = [documents[i] for i in order]

    return ids[:n_results], documents[:n_results], best_distance


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
    if not documents:
        return "No relevant tactical context found", None

    context_parts = []
    for i, doc in enumerate(documents):
        context_parts.append(f"[Tactical Intel {i + 1}]:\n{doc}")
    return "\n\n".join(context_parts), best_distance