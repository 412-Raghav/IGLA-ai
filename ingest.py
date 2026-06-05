from data.tactical_docs import TACTICAL_DOCUMENTS
from rag.embedder import get_or_create_collection

def ingest_documents():
    """
    Load all tactical documents into ChromaDB.
    Run this once to populate the vector database.
    Run again after adding new documents — upsert handles duplicates.
    """
    collection = get_or_create_collection()
    print(f"Ingesting {len(TACTICAL_DOCUMENTS)} tactical documents...")

    collection.upsert(
        ids=[doc["id"] for doc in TACTICAL_DOCUMENTS],
        documents=[doc["text"] for doc in TACTICAL_DOCUMENTS],
        metadatas=[doc["metadata"] for doc in TACTICAL_DOCUMENTS]
    )

    print(f"✅ {len(TACTICAL_DOCUMENTS)} documents ingested.")
    print("Vector database is ready for queries.")

if __name__ == "__main__":
    ingest_documents()