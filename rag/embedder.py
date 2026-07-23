import chromadb
from chromadb.utils import embedding_functions
from config import CHROMA_DB_PATH

chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

mpnet_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-mpnet-base-v2"
)


def get_or_create_collection():
    """
    Get existing ChromaDB collection or create a new one.
    A collection is like a table in a regular database.
    Ours holds all tactical documents as embeddings.
    """
    collection = chroma_client.get_or_create_collection(
        name="igla_tactical_knowledge",
        embedding_function=mpnet_ef,
        metadata={"hnsw:space": "cosine"}
    )
    return collection