# IGLA — In-Game Leader AI

RAG-powered tactical intelligence system for professional Valorant IGLs.

## What It Does

IGLA analyzes in-game situations and returns opponent-aware tactical 
responses. It retrieves team-specific intel from a vector database 
before every query — so responses are grounded in real scouting data, 
not general knowledge.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Anthropic Claude API |
| Vector DB | ChromaDB |
| Embeddings | all-MiniLM-L6-v2 |
| API | FastAPI + Uvicorn |
| Language | Python 3.13 |

## How It Works

1. IGL describes a game situation in natural language
2. IGLA searches ChromaDB for relevant opponent intel
3. Retrieved intel is injected into the prompt as context
4. Claude returns a specific, opponent-aware tactical response

## API Usage

Start the server:
```bash
uvicorn api:app --reload
```

Send a query:
```bash
curl -X POST http://127.0.0.1:8000/ask \
-H "Content-Type: application/json" \
-d '{"situation": "We are attacking B site on Haven against PRX"}'
```

## Project Structure

```
igla-ai/
├── data/tactical_docs.py   ← opponent intel documents
├── rag/
│   ├── embedder.py         ← ChromaDB connection
│   └── retriever.py        ← semantic search
├── api.py                  ← FastAPI endpoint
├── main.py                 ← core RAG pipeline
├── ingest.py               ← populates vector database
└── config.py               ← single source of truth
```

## Built By

Developed as a production GenAI engineering project.
Target clients: Professional esports organisations.