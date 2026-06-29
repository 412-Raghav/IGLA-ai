"""Dump every doc ID and a content snippet from the live collection.

Use this to (a) confirm the collection is actually populated and (b) read
the REAL doc IDs before trusting them in golden_queries.py. Empirical
verification before any score is allowed to mean anything.

Run from the project root:
    python -m evals.peek
"""

from rag.embedder import get_or_create_collection

SNIPPET_LEN = 80


def main():
    collection = get_or_create_collection()
    count = collection.count()
    print(f'Collection "{collection.name}" holds {count} docs.\n')

    if count == 0:
        print("EMPTY. Run ingestion first, or this is the wrong DB path.")
        return

    data = collection.get()  # no query -> returns everything
    for doc_id, document in zip(data["ids"], data["documents"]):
        snippet = document[:SNIPPET_LEN].replace("\n", " ")
        print(f"  {doc_id:>22}  {snippet}...")


if __name__ == "__main__":
    main()