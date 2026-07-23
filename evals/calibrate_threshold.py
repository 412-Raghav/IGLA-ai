"""Threshold calibration probe for the mpnet migration.

Prints best_distance for the golden (on-topic) queries vs a set of
deliberately off-topic ones, so SCOPE_THRESHOLD can be set in the gap
between them on mpnet's distance scale. Read-only; writes nothing.

    python -m evals.calibrate_threshold
"""

from evals.golden_queries import GOLDEN_QUERIES
from rag.retriever import retrieve_ranked

# Off-topic: plausible sentences that have NOTHING to do with our corpus.
# A well-set gate should REJECT these -- their best_distance should sit
# clearly above the on-topic queries'.
OFF_TOPIC = [
    "how do I bake sourdough bread",
    "python list comprehension tutorial",
    "weather forecast for tomorrow",
    "best budget gaming laptop 2026",
    "how to change a car tire",
]


def probe(label, queries, team_id):
    print(f"\n{label}")
    print("-" * 60)
    dists = []
    for query in queries:
        _, _, best = retrieve_ranked(query, n_results=1, team_id=team_id)
        dists.append(best)
        shown = f"{best:.3f}" if best is not None else "n/a"
        print(f"  dist={shown}  \"{query}\"")
    valid = [d for d in dists if d is not None]
    if valid:
        print(f"  -> max on this set: {max(valid):.3f}")
    return valid


def main():
    # On-topic queries are PRX-scoped, matching the golden set.
    on = probe(
        "ON-TOPIC (golden queries, should PASS)",
        [e["query"] for e in GOLDEN_QUERIES],
        team_id=624,
    )
    # Off-topic scoped the same way -- same retrieval path, junk queries.
    off = probe(
        "OFF-TOPIC (junk, should be REJECTED)",
        OFF_TOPIC,
        team_id=624,
    )

    print("\n" + "=" * 60)
    if on and off:
        hi_on, lo_off = max(on), min(off)
        print(f"  highest on-topic distance : {hi_on:.3f}")
        print(f"  lowest  off-topic distance: {lo_off:.3f}")
        if lo_off > hi_on:
            print(f"  clean gap -> set SCOPE_THRESHOLD between them "
                  f"(e.g. {(hi_on + lo_off) / 2:.3f})")
        else:
            print("  NO clean gap -- on- and off-topic overlap; "
                  "the gate can't cleanly separate them. Decide by hand.")


if __name__ == "__main__":
    main()