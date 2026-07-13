import os
from dotenv import load_dotenv
load_dotenv()
REFRESH_INTERVAL_HOURS = int(os.getenv("REFRESH_INTERVAL_HOURS", "24"))
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SCOPE_THRESHOLD = float(os.getenv("SCOPE_THRESHOLD", "0.75"))
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
POSTGRES_USER = os.getenv("POSTGRES_USER", "igla")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB", "igla_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
if not ANTHROPIC_API_KEY:
    raise ValueError(
        "ANTHROPIC_API_KEY not found."
        "CHECK .env file exists and has the key. "
    )
if not POSTGRES_PASSWORD:
    raise ValueError(
        "POSTGRES_PASSWORD not found. "
        "Check .env exists and has the Postgres credentials."
    )

MODEL_NAME = "claude-sonnet-4-6"
MAX_TOKENS = 1024