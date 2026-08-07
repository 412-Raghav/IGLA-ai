"""The app's single Limiter instance, isolated so both api.py and
upload_routes.py can import it without a circular import.

api.py imports upload_routes to mount its router. If the limiter lived in
api.py, upload_routes importing it back would close a loop that fails at
import time -- api is only half-initialized when it pulls the router in, so
the `limiter = Limiter(...)` line has not run yet. A leaf module that imports
nothing local has no such loop: both sides import the same instance from here,
and api.py is still the one that wires it to the app (app.state.limiter) and
registers the 429 handler.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Keyed on client IP. In-memory store: counts reset on restart and are
# per-replica -- fine for one replica; Redis is the multi-replica upgrade path.
limiter = Limiter(key_func=get_remote_address)