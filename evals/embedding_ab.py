"""One-variable A/B: does a stronger embedder move retrieval @k?

Reads the exact production docs, re-embeds them into a SEPARATE collection
with a candidate model, and runs the frozen golden set through the SAME
retrieve_ranked path against both. Only the embedding model differs -- same
docs, same queries, same re-rank -- so any delta is the model alone. The
production collection is READ ONLY here; nothing is written back to it.

Run from the project root (venv; zero tokens; local embeddings):
    python -m evals.embedding_ab
"""

from chromadb.utils import embedding_functions

from evals.golden_queries import GOLDEN_QUERIES
from evals.run_eval import K_VALUES, MAX_K, rank_of
from rag.embedder import chroma_client, get_or_create_collection
from rag.retriever import retrieve_ranked

CANDIDATE_MODEL = "all-mpnet-base-v2"
EXPERIMENT_COLLECTION = "igla_experiment_mpnet"


def build_experiment_collection(source):
    """Copy source's docs into a fresh collection embedded by the candidate.

    Pulls ids/documents/metadatas via .get() (NOT embeddings -- we re-embed),
    then upserts into a separate collection whose embedding function is the
    candidate model. Same client, different collection. Source is read only.
    """
    total = source.count()
    dump = source.get(include=["documents", "metadatas"], limit=total)
    print(f"Pulled {len(dump['ids'])} docs from production (expected {total}).")

    try:
        chroma_client.delete_collection(EXPERIMENT_COLLECTION)  # fresh each run
    except Exception:
        pass  # first run: nothing to delete (not-found class varies by version)

    candidate_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=CANDIDATE_MODEL
    )
    exp = chroma_client.create_collection(
        name=EXPERIMENT_COLLECTION,
        embedding_function=candidate_ef,
        metadata={"hnsw:space": "cosine"},
    )
    exp.upsert(
        ids=dump["ids"],
        documents=dump["documents"],
        metadatas=dump["metadatas"],
    )
    print(f"Re-embedded {exp.count()} docs with {CANDIDATE_MODEL}.\n")
    return exp


def score(collection):
    """Run the frozen golden set through retrieve_ranked against `collection`."""
    scored = []
    for entry in GOLDEN_QUERIES:
        expected_id = entry["expected_doc_id"]
        if expected_id is None:
            continue
        returned_ids, _, _ = retrieve_ranked(
            entry["query"],
            n_results=MAX_K,
            team_id=entry.get("team_id"),
            collection=collection,
        )
        scored.append(
            (entry["query"], expected_id, rank_of(expected_id, returned_ids))
        )
    return scored


def hit_rate(scored, k):
    hits = sum(1 for _, _, rank in scored if rank is not None and rank <= k)
    return hits, len(scored)


def main():
    prod = get_or_create_collection()
    exp = build_experiment_collection(prod)

    prod_scored = score(prod)
    exp_scored = score(exp)

    print(f"PER-QUERY on {CANDIDATE_MODEL}")
    print("-" * 60)
    for query, expected_id, rank in exp_scored:
        verdict = f"rank {rank}" if rank is not None else "MISS"
        print(f'  {verdict:<8} "{query}" -> {expected_id}')

    print("\n@k   MiniLM (prod)   vs   mpnet (candidate)")
    print("-" * 60)
    for k in K_VALUES:
        p_hits, n = hit_rate(prod_scored, k)
        e_hits, _ = hit_rate(exp_scored, k)
        p = p_hits / n * 100 if n else 0.0
        e = e_hits / n * 100 if n else 0.0
        print(f"  @{k}:   {p_hits}/{n} = {p:>3.0f}%        {e_hits}/{n} = {e:>3.0f}%")


if __name__ == "__main__":
    main()