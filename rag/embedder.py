import chromadb
from chromadb.utils import embedding_functions

default_ef = embedding_functions.DefaultEmbeddingFunction()
chroma_client = chromadb.PersistentClient(path="./chroma_db")

def get_or_create_collection():
    """
    Get existing ChromaDB collection or create a new one.
    A collection is like a table in a regular database.
    Ours holds all tactical documents as embeddings.
    """
    collection = chroma_client.get_or_create_collection(
        name="igla_tactical_knowledge",
        embedding_function=default_ef,
        metadata={"hnsw:space": "cosine"}
    )
    return collection