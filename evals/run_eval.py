"""IGLA retrieval evaluation harness.

Runs the golden query set against the LOCAL collection and reports
hit-rate@k: the fraction of queries whose expected doc lands in the
top-k retrieved results. No LLM is called -- this isolates the retriever
("the librarian") so a bad answer can be blamed on retrieval vs writing.

Run from the project root:
    python -m evals.run_eval
"""

import argparse

from config import SCOPE_THRESHOLD
from evals.golden_queries import GOLDEN_QUERIES
from rag.embedder import get_or_create_collection
from rag.retriever import passes_scope_gate, retrieve_ranked

# @1 = "was it the TOP result?" (strictest, single-best-answer).
# @3 = "was it in the top 3?" (what the writer actually sees as context).
K_VALUES = (1, 3)
MAX_K = max(K_VALUES)


def rank_of(expected_id, returned_ids):
    """Return 1-based rank of expected_id, or None if not retrieved."""
    if expected_id in returned_ids:
        return returned_ids.index(expected_id) + 1
    return None


def main(threshold=SCOPE_THRESHOLD):
    collection = get_or_create_collection()
    print(f"Collection holds {collection.count()} docs.")
    print(f"Scope-gate threshold: {threshold}\n")

    scored = []
    skipped = []

    for entry in GOLDEN_QUERIES:
        query = entry["query"]
        expected_id = entry["expected_doc_id"]

        if expected_id is None:
            skipped.append(query)
            continue

        returned_ids, _, top_distance = retrieve_ranked(
            query, n_results=MAX_K, team_id=entry.get("team_id")
        )

        rank = rank_of(expected_id, returned_ids)
        gate = "PASS" if passes_scope_gate(top_distance, threshold) else "REJECT"
        scored.append((query, expected_id, rank, top_distance, gate))

    print("PER-QUERY RESULTS")
    print("-" * 60)
    for query, expected_id, rank, top_distance, gate in scored:
        verdict = f"rank {rank}" if rank is not None else "MISS"
        dist = f"{top_distance:.3f}" if top_distance is not None else "n/a"
        print(f'  {verdict:<8} {gate:<6} dist={dist}  "{query}" -> {expected_id}')

    print("\nHIT-RATE SUMMARY")
    print("-" * 60)
    n = len(scored)
    for k in K_VALUES:
        hits = sum(
            1 for _, _, rank, _, _ in scored
            if rank is not None and rank <= k
        )
        served = sum(
            1 for _, _, rank, _, gate in scored
            if rank is not None and rank <= k and gate == "PASS"
        )
        rate = (hits / n * 100) if n else 0.0
        served_rate = (served / n * 100) if n else 0.0
        print(f"  hit-rate@{k}: {hits}/{n} = {rate:.0f}%")
        print(f"  served@{k}:   {served}/{n} = {served_rate:.0f}%"
              "   (rank@k AND scope-gate PASS)")

    if skipped:
        print(f"\nSkipped {len(skipped)} unset queries (None):")
        for query in skipped:
            print(f'  - "{query}"')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IGLA retrieval eval.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=SCOPE_THRESHOLD,
        help="Scope-gate threshold to score against (default: config value).",
    )
    args = parser.parse_args()
    main(threshold=args.threshold)