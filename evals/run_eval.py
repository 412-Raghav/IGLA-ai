"""IGLA retrieval evaluation harness.

Runs the golden query set against the LOCAL collection and reports
hit-rate@k: the fraction of queries whose expected doc lands in the
top-k retrieved results. No LLM is called -- this isolates the retriever
("the librarian") so a bad answer can be blamed on retrieval vs writing.

Run from the project root:
    python -m evals.run_eval
"""

from evals.golden_queries import GOLDEN_QUERIES
from rag.embedder import get_or_create_collection
from rag.retriever import _build_where

# @1 = "was it the TOP result?" (strictest, single-best-answer).
# @3 = "was it in the top 3?" (what the writer actually sees as context).
K_VALUES = (1, 3)
MAX_K = max(K_VALUES)


def rank_of(expected_id, returned_ids):
    """Return 1-based rank of expected_id, or None if not retrieved."""
    if expected_id in returned_ids:
        return returned_ids.index(expected_id) + 1
    return None


def main():
    collection = get_or_create_collection()
    print(f"Collection holds {collection.count()} docs.\n")

    scored = []
    skipped = []

    for entry in GOLDEN_QUERIES:
        query = entry["query"]
        expected_id = entry["expected_doc_id"]

        if expected_id is None:
            skipped.append(query)
            continue

        results = collection.query(
            query_texts=[query],
            n_results=MAX_K,
            where=_build_where(entry.get("team_id")),
        )
        returned_ids = results["ids"][0]
        distances = results["distances"][0]
        top_distance = distances[0] if distances else None

        rank = rank_of(expected_id, returned_ids)
        scored.append((query, expected_id, rank, top_distance))

    print("PER-QUERY RESULTS")
    print("-" * 60)
    for query, expected_id, rank, top_distance in scored:
        verdict = f"rank {rank}" if rank is not None else "MISS"
        dist = f"{top_distance:.3f}" if top_distance is not None else "n/a"
        print(f'  {verdict:<8} dist={dist}  "{query}" -> {expected_id}')

    print("\nHIT-RATE SUMMARY")
    print("-" * 60)
    n = len(scored)
    for k in K_VALUES:
        hits = sum(
            1 for _, _, rank, _ in scored
            if rank is not None and rank <= k
        )
        rate = (hits / n * 100) if n else 0.0
        print(f"  hit-rate@{k}: {hits}/{n} = {rate:.0f}%")

    if skipped:
        print(f"\nSkipped {len(skipped)} unset queries (None):")
        for query in skipped:
            print(f'  - "{query}"')


if __name__ == "__main__":
    main()