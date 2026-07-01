"""Verify team_id-scoped retrieval: the 9b keystone.

Asserts three properties the eval harness can't (it bypasses the filter):
  1. team_id=624 returns ONLY PRX docs + general docs (no Fnatic).
  2. team_id=2593 returns ONLY Fnatic docs + general docs (no PRX).
  3. team_id=None (default) returns the whole corpus unchanged.

Run from the project root:
    python -m evals.check_filter
"""

from rag.retriever import _build_where
from rag.embedder import get_or_create_collection


def ids_for(collection, team_id, n=13):
    """Return the set of doc IDs retrieved under a given team filter."""
    results = collection.query(
        query_texts=["team tactical tendencies"],
        n_results=n,
        where=_build_where(team_id),
    )
    return set(results["ids"][0])


def main():
    collection = get_or_create_collection()

    prx = ids_for(collection, 624)
    fnc = ids_for(collection, 2593)
    everything = ids_for(collection, None)

    general = {"sage_killjoy_combo", "low_time_defense"}
    fnc_docs = {"fnc_defensive_style", "fnc_ascent_defense"}
    prx_static = {
        "prx_general_style", "prx_lotus_a_execute",
        "prx_haven_style", "prx_economy_habits",
    }

    print("PRX filter (624) retrieved:", len(prx), "docs")
    print("  contains general docs:", general <= prx)
    print("  excludes Fnatic docs :", fnc_docs.isdisjoint(prx))

    print("\nFnatic filter (2593) retrieved:", len(fnc), "docs")
    print("  contains general docs:", general <= fnc)
    print("  excludes PRX static  :", prx_static.isdisjoint(fnc))

    print("\nNo filter (None) retrieved:", len(everything), "docs")
    print("  sees full corpus:", len(everything) == collection.count())


if __name__ == "__main__":
    main()