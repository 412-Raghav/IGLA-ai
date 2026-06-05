from rag.embedder import get_or_create_collection

def  retrieve_context(query: str, n_results: int =3) -> str:
    """
    Search vector database for relevant tactical context.
    Takes user query, returns formatted relevant documents.

    Args:
        query: The tactical situation from the user
        n_results: How many relevant docs to retrieve (default 3)

    Returns:
        Formatted string of relevant tactical intel
    """
    collection = get_or_create_collection()

    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    if not results["documents"][0]:
        return "No relevant tactical context found"

    context_parts = []
    for i, doc in enumerate(results["documents"][0]):
        context_parts.append(f"[Tactical Intel {i+1}]:\n{doc}")
    return "\n\n".join(context_parts)