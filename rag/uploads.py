import re
import uuid
from datetime import datetime, timezone

from chromadb.errors import NotFoundError

from rag.embedder import chroma_client, mpnet_ef

PROVENANCE_UPLOAD = "user-uploaded"

_CHUNK_CHARS = 800
_CHUNK_OVERLAP = 120


def get_or_create_user_collection(user_id: int):
    """Get-or-create the private upload collection for one user.

    Each user's uploaded intel lives in its own ChromaDB collection, named
    from their server-assigned id. Isolation is structural -- a query only
    ever opens the caller's own collection, so one analyst's notes can never
    surface in another's retrieval, and there is no shared where-filter
    clause to get wrong.

    Built with the SAME embedding function (mpnet_ef) and distance space
    (cosine) as the shared tactical collection, so uploaded chunks and the
    curated/generated/live corpus sit in one comparable vector space --
    required for S2 to merge results across both collections by distance.
    """
    return chroma_client.get_or_create_collection(
        name=f"uploads_user_{user_id}",
        embedding_function=mpnet_ef,
        metadata={"hnsw:space": "cosine"},
    )


def get_user_collection(user_id: int):
    """Open a user's upload collection for reading. Never creates.

    Writers call get_or_create_user_collection; readers call this. The
    serving path must not write: get-or-create on every turn would leave an
    empty collection behind for every account that has never uploaded, which
    is a write on a read path and clutter that nothing ever cleans up.

    Returns None when the user has no uploads, which doubles as the
    zero-uploads fast path -- the caller skips the second query entirely
    rather than paying an embed to search nothing.

    NotFoundError is caught specifically (measured against chromadb 1.5.9:
    get_collection raises, it does not return None). A bare except here would
    turn a real Chroma outage into "this user has no uploads" -- the query
    would quietly serve shared-corpus-only results and nothing would report
    that half the retrieval was missing.

    embedding_function is passed explicitly rather than left to Chroma's
    default. The default is MiniLM at 384 dimensions; these collections are
    mpnet at 768. Relying on Chroma to restore the stored function is a
    guess, and the failure it protects against is the dimension-mismatch
    class that already has an open finding against it.
    """
    try:
        return chroma_client.get_collection(
            name=f"uploads_user_{user_id}",
            embedding_function=mpnet_ef,
        )
    except NotFoundError:
        return None


def chunk_text(
    text: str, chunk_chars: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP
) -> list[str]:
    """Split an uploaded note into embeddable chunks.

    Paragraph-aware: splits on blank lines first, so a short self-contained
    paragraph stays one chunk. Any paragraph longer than chunk_chars is
    hard-wrapped with overlap, because the embedder silently truncates long
    input (mpnet past 384 word-pieces, MiniLM past 256) -- an over-long chunk
    would lose its tail with no error. 800 chars is a conservative budget:
    ~200 tokens, comfortably under mpnet's 384 ceiling we build against.

    Internal whitespace in each paragraph is collapsed to single spaces so the
    char budget is predictable and wrap points land on real word boundaries.
    """
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        if len(para) <= chunk_chars:
            chunks.append(para)
        else:
            chunks.extend(_wrap(para, chunk_chars, overlap))
    return chunks


def ingest_upload(
    user_id: int, team_id: int, filename: str, text: str
) -> dict:
    """Chunk one uploaded note and write it to the user's private collection.

    Takes already-decoded text, not raw bytes: a malformed upload is a client
    error, so decoding belongs at the API boundary where it can become a 400
    rather than surfacing as a 500 from deep in the ingest path.

    Returns a summary the route can hand straight back to the caller. A note
    with no usable text yields zero chunks and writes nothing -- ChromaDB
    rejects an empty add -- leaving the "that's a 400" decision to the route.

    Args:
        user_id: Owner. Selects the collection; isolation is structural.
        team_id: The opponent this note is about. MUST be int -- ChromaDB
            where-filters are exact-type, so a string "624" stored here would
            silently never match an int 624 filter in S2's retrieval.
        filename: Original name, kept for display and for grouping.
        text: Decoded note contents.

    Returns:
        {"upload_id": str, "chunks": int, "source": str}
    """
    chunks = chunk_text(text)
    upload_id = uuid.uuid4().hex
    if not chunks:
        return {"upload_id": upload_id, "chunks": 0, "source": filename}

    uploaded_at = datetime.now(timezone.utc).isoformat()
    collection = get_or_create_user_collection(user_id)
    collection.add(
        ids=[f"up_{upload_id}_{i:04d}" for i in range(len(chunks))],
        documents=chunks,
        metadatas=[
            {
                "provenance": PROVENANCE_UPLOAD,
                "team_id": team_id,
                "upload_id": upload_id,
                "source": filename,
                "chunk_index": i,
                "uploaded_at": uploaded_at,
            }
            for i in range(len(chunks))
        ],
    )
    return {"upload_id": upload_id, "chunks": len(chunks), "source": filename}


def _wrap(text: str, chunk_chars: int, overlap: int) -> list[str]:
    """Sliding-window wrap of one over-long paragraph.

    Each window is at most chunk_chars, snapped back to the last space so words
    aren't cut mid-token; the next window starts `overlap` chars before the cut
    so context bridges the seam. start always advances (chunk_chars > overlap),
    so a pathological input can't loop.
    """
    pieces: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = start + chunk_chars
        if end >= n:
            pieces.append(text[start:].strip())
            break
        cut = text.rfind(" ", start, end)
        if cut <= start:  # no space in window (one very long token): hard cut
            cut = end
        pieces.append(text[start:cut].strip())
        start = max(cut - overlap, start + 1)
    return [p for p in pieces if p]