import os
from dotenv import load_dotenv
load_dotenv()
REFRESH_INTERVAL_HOURS = int(os.getenv("REFRESH_INTERVAL_HOURS", "24"))
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SCOPE_THRESHOLD = float(os.getenv("SCOPE_THRESHOLD", "0.75"))
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
if not ANTHROPIC_API_KEY:
    raise ValueError(
        "ANTHROPIC_API_KEY not found."
        "CHECK .env file exists and has the key. "
    )

MODEL_NAME = "claude-sonnet-4-6"
MAX_TOKENS = 1024