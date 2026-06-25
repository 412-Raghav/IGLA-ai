from rag.embedder import get_or_create_collection


def retrieve_context(query: str, n_results: int = 3) -> tuple[str, float | None]:
    """Search the vector database for relevant tactical context.

    Returns a (context, best_distance) pair. best_distance is the cosine
    distance of the closest-matching document — smaller means more relevant.
    It is None when the collection returns nothing, so the caller can tell
    "empty database" apart from "a genuine but weak match."

    Args:
        query: The tactical situation from the user.
        n_results: How many documents to retrieve (default 3).

    Returns:
        (formatted_context, best_distance).
    """
    collection = get_or_create_collection()

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
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