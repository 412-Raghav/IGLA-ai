import os
from dotenv import load_dotenv
load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError(
        "ANTHROPIC_API_KEY not found."
        "CHEck .env file exists and has the key. "
    )

MODEL_NAME = "claude-sonnet-4-6"
MAX_TOKENS = 1024