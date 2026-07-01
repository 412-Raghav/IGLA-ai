from rag.embedder import get_or_create_collection


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
    collection = get_or_create_collection()

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where=_build_where(team_id),
        include=["documents", "distances"],
    )

    documents = results["documents"][0]
    distances = results["distances"][0]

    if not documents:
        return "No relevant tactical context found", None

    best_distance = distances[0]

    context_parts = []
    for i, doc in enumerate(documents):
        context_parts.append(f"[Tactical Intel {i + 1}]:\n{doc}")
    return "\n\n".join(context_parts), best_distance