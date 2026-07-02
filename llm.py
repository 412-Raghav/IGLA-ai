"""Shared Anthropic client — single source of truth for LLM access.

Both the query-time reasoner (main.py) and the build-time doc
generator (data/generate_docs.py) import this one client instead of
constructing their own. Construction wiring lives here and nowhere
else.
"""

import anthropic

from config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)