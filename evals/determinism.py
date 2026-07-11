"""Is retrieval repeatable? Within one process, and across processes.

Distinguishes two causes of the observed rank flip on "primary initiator":
an approximate (HNSW) search returning different neighbours run to run,
versus the embedding of the same string differing run to run.
"""
from rag.embedder import get_or_create_collection, mpnet_ef
from rag.retriever import retrieve_ranked

QUERY = "primary initiator"
TEAM_ID = 624
TRIALS = 20


def main():
    collection = get_or_create_collection()

    vectors = [tuple(mpnet_ef([QUERY])[0]) for _ in range(3)]
    print(f"embedding deterministic in-process: {len(set(vectors)) == 1}")

    seen = {}
    for _ in range(TRIALS):
        ids, _, best_distance = retrieve_ranked(
            QUERY, n_results=3, team_id=TEAM_ID, collection=collection
        )
        key = (ids[0], round(best_distance, 6))
        seen[key] = seen.get(key, 0) + 1

    print(f"\n{TRIALS} queries, one process, one collection object:")
    for (top_id, dist), count in sorted(seen.items(), key=lambda x: -x[1]):
        print(f"  {count:>3}x  top={top_id:<20} best_distance={dist}")
    print(f"\ndistinct outcomes: {len(seen)}")


if __name__ == "__main__":
    main()