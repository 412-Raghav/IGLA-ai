"""Verify team_id-scoped retrieval: the 9b keystone.

Corpus-size agnostic. For every tracked team, asserts that a team_id
filter returns ONLY that team's docs plus the universal 'general'
shelf -- never another team's docs. Also asserts the unfiltered path
returns the whole corpus. The eval harness can't check this (it
bypasses the filter), so this is the keystone's dedicated regression.

Run from the project root:
    python -m evals.check_filter
"""

from rag.retriever import _build_where
from rag.embedder import get_or_create_collection
from data.team_registry import TRACKED_TEAMS


def team_id_of(metadata):
    """Return a doc's team_id, or None if it's a general (scope) doc."""
    return metadata.get("team_id")


def is_general(metadata):
    """True if the doc is on the universal shelf (no team)."""
    return metadata.get("scope") == "general"


def main():
    collection = get_or_create_collection()
    total = collection.count()
    print(f"Collection holds {total} docs.\n")

    # Pull every doc's metadata once so we can reason about the corpus
    # without re-querying per team.
    everything = collection.get(include=["metadatas"])
    all_meta = everything["metadatas"]
    general_count = sum(1 for m in all_meta if is_general(m))

    all_pass = True

    for team in TRACKED_TEAMS:
        tid = team["team_id"]
        name = team["name"]

        # Ask for the full corpus size so nothing is missed past a cutoff.
        results = collection.query(
            query_texts=["team tactical tendencies and playstyle"],
            n_results=total,
            where=_build_where(tid),
        )
        returned_meta = results["metadatas"][0]

        # Every returned doc must belong to THIS team or be general.
        wrong_team = [
            m for m in returned_meta
            if not is_general(m) and team_id_of(m) != tid
        ]
        has_general = any(is_general(m) for m in returned_meta)

        ok = not wrong_team and has_general
        all_pass = all_pass and ok

        flag = "PASS" if ok else "FAIL"
        print(
            f"  [{flag}] {name:<16} (id {tid:<6}) "
            f"returned {len(returned_meta):<3} docs, "
            f"general included: {has_general}, "
            f"foreign-team docs: {len(wrong_team)}"
        )

    # Unfiltered path must see the entire corpus.
    unfiltered = collection.query(
        query_texts=["team tactical tendencies and playstyle"],
        n_results=total,
        where=_build_where(None),
    )
    unfiltered_count = len(unfiltered["metadatas"][0])
    corpus_ok = unfiltered_count == total
    all_pass = all_pass and corpus_ok

    print(
        f"\n  [{'PASS' if corpus_ok else 'FAIL'}] unfiltered path "
        f"returned {unfiltered_count}/{total} docs "
        f"({general_count} general docs on the shelf)"
    )

    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")


if __name__ == "__main__":
    main()