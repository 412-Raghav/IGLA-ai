from rag.embedder import get_or_create_collection
from rag.retriever import _build_where

PROBES = ["best duelist", "main controller", "scouting overview of the team"]
PRX_ID = 624


def main():
    collection = get_or_create_collection()

    for probe in PROBES:
        results = collection.query(
            query_texts=[probe],
            n_results=3,
            where=_build_where(PRX_ID),
            include=["metadatas", "distances"],
        )
        ids = results["ids"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        print(f'\nPROBE: "{probe}"  (scoped to team_id={PRX_ID})')
        print("-" * 60)
        for rank, (doc_id, meta, dist) in enumerate(
            zip(ids, metas, dists), start=1
        ):
            source = meta.get("source", "?")
            print(f"  {rank}. dist={dist:.3f}  [{source:<10}] {doc_id}")


if __name__ == "__main__":
    main()