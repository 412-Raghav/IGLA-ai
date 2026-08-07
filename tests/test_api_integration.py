"""HTTP-level integration tests: the app driven through FastAPI's TestClient.

Where the service-layer suite calls chat_service functions directly, this
suite exercises the real request path -- routing, the session-cookie
dependency, Pydantic validation, status codes -- by making HTTP calls against
the app in-process. No uvicorn: TestClient runs the ASGI app itself, so there
is no separate server, no port, and no stale-process ambiguity.

DB seam: the `client` fixture overrides get_db with the savepoint-rolled-back
session from conftest, so every request -- and require_user -- shares the one
connection the `db` fixture holds inside an open transaction. Nothing a
request commits survives teardown.

Mock seam (/ask only): retrieve_merged and ask_igla are patched in the `api`
namespace -- where /ask looks them up, since api.py imports them by name. This
fires zero Anthropic calls and lets a test drive the scope gate by choosing
best_distance: retrieve_merged returns a 4-tuple (ids, documents,
best_distance, origins), and passes_scope_gate passes when best_distance <=
SCOPE_THRESHOLD (0.75). passes_scope_gate and format_context are left REAL --
the gate and the prompt formatter are part of the contract under test.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from data.team_registry import TRACKED_TEAMS, TRACKED_TEAM_IDS
from main import REJECTION_MESSAGE
from rag.retriever import format_context
from upload_routes import MAX_UPLOAD_BYTES

# Every test in this module drives the app through TestClient, which imports
# api.py and its heavy transitive graph. Marking the whole module lets
# `pytest -m "not integration"` skip that import and run the fast unit loop.
pytestmark = pytest.mark.integration

# Derived from the registry SSOT, never hardcoded: the first tracked team's id
# is a valid target, and one past the highest id is guaranteed untracked by
# construction. If the registry changes, both follow automatically.
TRACKED_TEAM_ID = TRACKED_TEAMS[0]["team_id"]
UNTRACKED_TEAM_ID = max(TRACKED_TEAM_IDS) + 1


def _register_and_login(client, username: str, password: str = "testpass123"):
    """Register a user and log them in; the client's cookie jar now carries
    their session. Pure setup for the CRUD tests -- the auth tests deliberately
    inline these calls because they are what those tests exercise. Returns the
    created user's id.
    """
    reg = client.post(
        "/auth/register", json={"username": username, "password": password}
    )
    assert reg.status_code == 201
    login = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200
    return reg.json()["id"]


def _create_thread(client, team_id: int = TRACKED_TEAM_ID) -> str:
    """Create a thread as the currently-logged-in user; return its id.
    Setup for the /ask tests, which need a real conversation to post into.
    """
    created = client.post("/conversations", json={"team_id": team_id})
    assert created.status_code == 201
    return created.json()["id"]


@pytest.fixture(autouse=True)
def _limiter_disabled():
    """Disable the IP rate limiter for every test in this module by default.

    slowapi's in-memory counter lives for the process and keys on the fixed
    'testclient' address, so an enabled limiter would let one test's request
    count bleed into the next. Disabled, the decorator is a pass-through that
    never touches the store: the ordinary tests stay hermetic, and the two that
    assert a 429 opt back in via `rate_limited`. No existing test asserts a 429,
    so disabling changes none of them -- it only removes their latent coupling.
    """
    from api import limiter

    limiter.enabled = False
    yield


@pytest.fixture
def rate_limited(_limiter_disabled):
    """Enable the limiter for one test, then disable it again.

    Depends on `_limiter_disabled` so ordering is fixed: disabled runs first,
    then this flips it on. Only one test per endpoint enables the limiter, so
    each 429 test's counter starts at zero with no reset -- a disabled limiter
    never incremented it. Teardown restores the module default so the next
    test's autouse sees a clean slate regardless of run order.
    """
    from api import limiter

    limiter.enabled = True
    yield
    limiter.enabled = False


def test_protected_route_rejects_missing_cookie(client):
    """A guarded route with no session cookie is a clean 401, before any DB
    access -- require_user raises the moment the cookie is absent."""
    response = client.get("/conversations")
    assert response.status_code == 401


def test_auth_round_trip(client):
    """register -> login -> /me -> logout -> /me: the full cookie lifecycle.

    Proves the seam end to end: login's Set-Cookie lands in TestClient's jar,
    rides back on /me through the overridden get_db (same connection, so
    require_user sees the session row login committed), and is cleared by
    logout so the final /me is a clean 401.
    """
    creds = {"username": "roundtrip_user", "password": "testpass123"}

    reg = client.post("/auth/register", json=creds)
    assert reg.status_code == 201
    assert reg.json()["username"] == "roundtrip_user"
    assert isinstance(reg.json()["id"], int)

    login = client.post("/auth/login", json=creds)
    assert login.status_code == 200
    assert login.json() == {"status": "logged in", "username": "roundtrip_user"}
    assert client.cookies.get("igla_session") is not None

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "roundtrip_user"

    logout = client.post("/auth/logout")
    assert logout.status_code == 200

    me_after = client.get("/auth/me")
    assert me_after.status_code == 401


def test_login_wrong_password_is_401(client):
    """A registered user with the wrong password is a clean 401, no cookie."""
    client.post(
        "/auth/register",
        json={"username": "wrongpw_user", "password": "correct-horse"},
    )
    login = client.post(
        "/auth/login",
        json={"username": "wrongpw_user", "password": "wrong-horse"},
    )
    assert login.status_code == 401
    assert client.cookies.get("igla_session") is None


def test_register_duplicate_username_is_409(client):
    """The second registration of a taken username is 409, not a crash."""
    creds = {"username": "dup_user", "password": "testpass123"}
    first = client.post("/auth/register", json=creds)
    assert first.status_code == 201
    second = client.post("/auth/register", json=creds)
    assert second.status_code == 409


def test_create_conversation(client):
    """Creating a thread returns 201 with the expected shape: id as a string
    (the UUID is str()-ed for JSON), the anchor team echoed, title still null
    until the first turn names it."""
    _register_and_login(client, "creator_user")
    resp = client.post("/conversations", json={"team_id": TRACKED_TEAM_ID})
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], str)
    assert body["team_id"] == TRACKED_TEAM_ID
    assert body["title"] is None


def test_conversation_lifecycle(client):
    """create -> appears in list -> get -> delete -> gone.

    A fresh thread with no turns reports current_team_id == its birth team
    (get_current_anchor falls back to the anchor when no user turn exists) and
    an empty messages list.
    """
    _register_and_login(client, "lifecycle_user")

    created = client.post("/conversations", json={"team_id": TRACKED_TEAM_ID})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    listed = client.get("/conversations")
    assert listed.status_code == 200
    ids = [c["id"] for c in listed.json()["conversations"]]
    assert conversation_id in ids

    fetched = client.get(f"/conversations/{conversation_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["id"] == conversation_id
    assert body["current_team_id"] == TRACKED_TEAM_ID
    assert body["messages"] == []

    deleted = client.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 204

    gone = client.get(f"/conversations/{conversation_id}")
    assert gone.status_code == 404


def test_create_conversation_untracked_team_is_422(client):
    """An untracked team_id is rejected at the NewConversation validator -- a
    clean 422 at the boundary, so an unknown anchor never births a thread."""
    _register_and_login(client, "untracked_user")
    resp = client.post("/conversations", json={"team_id": UNTRACKED_TEAM_ID})
    assert resp.status_code == 422


def test_other_users_conversation_is_404_not_403(client):
    """A thread owned by someone else is 404 for both get and delete -- never
    403. 403 would confirm the row exists (an enumeration signal); the service
    layer's ownership WHERE clause collapses "absent" and "not yours" into one
    answer this layer cannot tell apart.

    The two users share one client: the intruder's login overwrites the
    session cookie in TestClient's jar, so the client simply becomes them --
    no manual cookie juggling, exactly like a second browser.
    """
    _register_and_login(client, "owner_user")
    created = client.post("/conversations", json={"team_id": TRACKED_TEAM_ID})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    _register_and_login(client, "intruder_user")

    assert client.get(f"/conversations/{conversation_id}").status_code == 404
    assert client.delete(f"/conversations/{conversation_id}").status_code == 404


def test_absent_conversation_is_404(client):
    """A well-formed UUID that names no thread is 404, not a 500."""
    _register_and_login(client, "absent_user")
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/conversations/{missing}").status_code == 404


def test_malformed_conversation_id_is_422(client):
    """A path segment that is not a UUID fails path validation with 422 before
    the handler runs -- the caller is authenticated, so this is validation,
    not auth."""
    _register_and_login(client, "malformed_user")
    assert client.get("/conversations/not-a-uuid").status_code == 422


def test_ask_pass_branch_generates_and_persists(client):
    """A relevant question: retrieve -> gate PASS -> generate -> persist.

    retrieve_merged is mocked to a low best_distance (0.20 <= 0.75) so the
    REAL passes_scope_gate passes; ask_igla is mocked to a sentinel answer so
    no Anthropic call fires. The turn is persisted -- a follow-up GET shows the
    user question and the assistant sentinel back on the thread.
    """
    _register_and_login(client, "ask_pass_user")
    conversation_id = _create_thread(client)

    documents = ["Paper Rex run a double-controller setup on Split."]
    with patch(
        "api.retrieve_merged",
        return_value=(["doc1"], documents, 0.20, ["corpus"]),
    ), patch("api.ask_igla", return_value="SENTINEL ANSWER") as mock_ask:
        resp = client.post(
            "/ask",
            json={"conversation_id": conversation_id, "message": "How do PRX defend Split?"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["gate"] == "pass"
    assert body["response"] == "SENTINEL ANSWER"
    assert body["origins"] == ["corpus"]

    # The REAL format_context ran and its output was threaded into generation:
    # ask_igla's second positional arg is the formatted block, not raw docs.
    mock_ask.assert_called_once()
    assert mock_ask.call_args.args[1] == format_context(documents)

    # The exchange was persisted -- reload shows both turns.
    reloaded = client.get(f"/conversations/{conversation_id}").json()
    roles = [m["role"] for m in reloaded["messages"]]
    contents = [m["content"] for m in reloaded["messages"]]
    assert roles == ["user", "assistant"]
    assert "SENTINEL ANSWER" in contents


def test_ask_reject_branch_does_not_call_model(client):
    """An out-of-scope question: gate REJECT on both attempts, no generation.

    retrieve_merged is mocked to a high best_distance (0.95 > 0.75). Because a
    return_value mock answers every call the same way, BOTH attempt 1 and the
    anchor-rewrite retry reject, landing in the real reject branch. The
    assertion that matters: ask_igla is NEVER called -- a rejected turn must
    not spend a token.
    """
    _register_and_login(client, "ask_reject_user")
    conversation_id = _create_thread(client)

    with patch(
        "api.retrieve_merged", return_value=([], [], 0.95, [])
    ), patch("api.ask_igla") as mock_ask:
        resp = client.post(
            "/ask",
            json={"conversation_id": conversation_id, "message": "What is the capital of France?"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["gate"] == "reject"
    assert body["response"] == REJECTION_MESSAGE
    assert body["origins"] == []
    mock_ask.assert_not_called()


def test_ask_upstream_model_error_is_502(client):
    """When Claude raises, /ask maps it to 502 -- not an unhandled 500.

    Gate passes (low best_distance) so control reaches generation; ask_igla is
    mocked to raise anthropic.APIError, and the endpoint's except clause turns
    it into a clean 502. This proves an upstream failure degrades to a status
    code instead of leaking a stack trace.
    """
    _register_and_login(client, "ask_502_user")
    conversation_id = _create_thread(client)

    error = anthropic.APIError(
        message="boom", request=None, body=None
    )
    with patch(
        "api.retrieve_merged",
        return_value=(["doc1"], ["some intel"], 0.20, ["corpus"]),
    ), patch("api.ask_igla", side_effect=error):
        resp = client.post(
            "/ask",
            json={"conversation_id": conversation_id, "message": "How do PRX defend Split?"},
        )

    assert resp.status_code == 502


def test_ask_other_users_conversation_is_404(client):
    """/ask into a thread you don't own is 404 -- ownership on the ask path,
    same WHERE-clause guarantee as get/delete, checked before any retrieval."""
    _register_and_login(client, "ask_owner_user")
    conversation_id = _create_thread(client)

    _register_and_login(client, "ask_intruder_user")
    resp = client.post(
        "/ask",
        json={"conversation_id": conversation_id, "message": "How do PRX defend Split?"},
    )
    assert resp.status_code == 404


def test_ask_blank_message_is_422(client):
    """A whitespace-only message fails the AskRequest validator with 422 before
    retrieval or generation -- a blank question never reaches the pipeline."""
    _register_and_login(client, "ask_blank_user")
    conversation_id = _create_thread(client)
    resp = client.post(
        "/ask",
        json={"conversation_id": conversation_id, "message": "   "},
    )
    assert resp.status_code == 422


# --- /uploads: mocked at the ChromaDB boundary -------------------------------
# ingest_upload and get_user_collection are patched in the `upload_routes`
# namespace -- where the route looks them up -- so no test in this group writes
# to the on-disk chroma_db/. The route's four boundary guards (415/422/413/400)
# all reject BEFORE ingest_upload, so they need no patch; only the happy path,
# the empty-note 400, and the list/delete tests touch the (mocked) collection.


def test_upload_bad_extension_is_415(client):
    """Extension is checked first: a .pdf is rejected before team_id, before a
    byte is read, before ingest -- so a bad type never pays for anything."""
    _register_and_login(client, "upload_badext_user")
    resp = client.post(
        "/uploads",
        files={"file": ("report.pdf", b"whatever", "application/pdf")},
        data={"team_id": str(TRACKED_TEAM_ID)},
    )
    assert resp.status_code == 415


def test_upload_untracked_team_is_422(client):
    """The hand-written tracked-team guard: an untracked team_id is 422. This
    guard has no Pydantic validator behind it (form fields don't flow through
    one), so this test is what proves it still exists."""
    _register_and_login(client, "upload_untracked_user")
    resp = client.post(
        "/uploads",
        files={"file": ("notes.txt", b"some intel", "text/plain")},
        data={"team_id": str(UNTRACKED_TEAM_ID)},
    )
    assert resp.status_code == 422


def test_upload_too_large_is_413(client):
    """Size is enforced on bytes actually read (one past the cap), not on the
    client-supplied Content-Length -- so a payload over the limit is 413."""
    _register_and_login(client, "upload_toobig_user")
    oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
    resp = client.post(
        "/uploads",
        files={"file": ("big.txt", oversized, "text/plain")},
        data={"team_id": str(TRACKED_TEAM_ID)},
    )
    assert resp.status_code == 413


def test_upload_non_utf8_is_400(client):
    """Bytes that are not valid UTF-8 are a clean 400 at the boundary, not a
    500 from deep in ingest -- decoding happens here, before ingest_upload."""
    _register_and_login(client, "upload_baddecode_user")
    resp = client.post(
        "/uploads",
        files={"file": ("bad.txt", b"\xff\xff\xff", "text/plain")},
        data={"team_id": str(TRACKED_TEAM_ID)},
    )
    assert resp.status_code == 400


def test_upload_empty_note_is_400(client):
    """A note that yields zero usable chunks is a 400. ingest_upload is mocked
    to report chunks=0 (ChromaDB rejects an empty add), and the route turns
    that into 'no readable text' rather than writing nothing silently."""
    _register_and_login(client, "upload_empty_user")
    summary = {"upload_id": "up_empty", "chunks": 0, "source": "blank.txt"}
    with patch("upload_routes.ingest_upload", return_value=summary):
        resp = client.post(
            "/uploads",
            files={"file": ("blank.txt", b"   ", "text/plain")},
            data={"team_id": str(TRACKED_TEAM_ID)},
        )
    assert resp.status_code == 400


def test_upload_happy_path_persists_summary(client):
    """A valid note: the route decodes the bytes (utf-8-sig) and forwards
    (user_id, team_id, truncated filename, decoded text) to ingest_upload,
    then returns the summary plus the echoed team_id. ingest_upload is mocked,
    so no real embed or disk write happens."""
    user_id = _register_and_login(client, "upload_happy_user")
    summary = {"upload_id": "up_happy", "chunks": 3, "source": "prx_notes.txt"}
    with patch(
        "upload_routes.ingest_upload", return_value=summary
    ) as mock_ingest:
        resp = client.post(
            "/uploads",
            files={"file": ("prx_notes.txt", b"Paper Rex play fast.", "text/plain")},
            data={"team_id": str(TRACKED_TEAM_ID)},
        )

    assert resp.status_code == 201
    assert resp.json() == {
        "upload_id": "up_happy",
        "chunks": 3,
        "source": "prx_notes.txt",
        "team_id": TRACKED_TEAM_ID,
    }
    mock_ingest.assert_called_once_with(
        user_id, TRACKED_TEAM_ID, "prx_notes.txt", "Paper Rex play fast."
    )


def test_upload_unauthenticated_is_401(client):
    """No session cookie: the require_user guard 401s. The multipart body is
    spooled before the dependency resolves, but the guard reliably stops ingest
    -- nothing is written."""
    resp = client.post(
        "/uploads",
        files={"file": ("notes.txt", b"some intel", "text/plain")},
        data={"team_id": str(TRACKED_TEAM_ID)},
    )
    assert resp.status_code == 401


def test_list_uploads_empty(client):
    """A user who has never uploaded has no collection: get_user_collection
    returns None and the route returns an empty list, not an error."""
    _register_and_login(client, "list_empty_user")
    with patch("upload_routes.get_user_collection", return_value=None):
        resp = client.get("/uploads")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_uploads_folds_chunks(client):
    """One note is several chunks sharing an upload_id; the route folds them
    back into a single summary with a chunk count. The mocked collection
    returns two chunks of one note."""
    _register_and_login(client, "list_notes_user")
    meta_a = {
        "upload_id": "abc123",
        "source": "prx_notes.txt",
        "team_id": TRACKED_TEAM_ID,
        "uploaded_at": "2026-08-01T12:00:00+00:00",
        "chunk_index": 0,
        "provenance": "user-uploaded",
    }
    meta_b = {**meta_a, "chunk_index": 1}
    fake = MagicMock()
    fake.get.return_value = {"metadatas": [meta_a, meta_b]}

    with patch("upload_routes.get_user_collection", return_value=fake):
        resp = client.get("/uploads")

    assert resp.status_code == 200
    notes = resp.json()
    assert len(notes) == 1
    assert notes[0]["upload_id"] == "abc123"
    assert notes[0]["source"] == "prx_notes.txt"
    assert notes[0]["team_id"] == TRACKED_TEAM_ID
    assert notes[0]["chunks"] == 2


def test_delete_upload_removes_chunks(client):
    """Deleting an existing note: the route reads the note's chunks first (to
    confirm existence and count them), then deletes by upload_id and returns
    204. Existence is checked before delete because Chroma's delete is a silent
    no-op on no match."""
    _register_and_login(client, "delete_ok_user")
    upload_id = "abc123"
    fake = MagicMock()
    fake.get.return_value = {"ids": ["up_abc123_0000", "up_abc123_0001"]}

    with patch("upload_routes.get_user_collection", return_value=fake):
        resp = client.delete(f"/uploads/{upload_id}")

    assert resp.status_code == 204
    fake.get.assert_called_once_with(where={"upload_id": upload_id})
    fake.delete.assert_called_once_with(where={"upload_id": upload_id})


def test_delete_absent_upload_is_404(client):
    """Deleting an upload_id with no matching chunks is 404, never 403 -- 403
    would confirm the note exists (enumeration). The existence check finds no
    ids and the route 404s WITHOUT calling delete, so no silent no-op fires."""
    _register_and_login(client, "delete_absent_user")
    fake = MagicMock()
    fake.get.return_value = {"ids": []}

    with patch("upload_routes.get_user_collection", return_value=fake):
        resp = client.delete("/uploads/does-not-exist")

    assert resp.status_code == 404
    fake.delete.assert_not_called()


# --- rate limiting + /refresh single-flight ----------------------------------
# The limiter is disabled by default (autouse _limiter_disabled); the two 429
# tests opt in via `rate_limited`. REFRESH_TOKEN is patched to a known value so
# the token check never depends on the .env, and _trigger_refresh (or
# refresh_live_data) is patched so no real scrape thread or network call fires.

REFRESH_TOKEN_VALUE = "test-refresh-token"


def test_upload_rate_limit_returns_429_past_the_cap(client, rate_limited):
    """The sixth upload inside the window is 429; the first five are 201.

    ingest_upload is mocked so the five accepted uploads do no real embedding.
    The limiter check runs in the endpoint wrapper -- ahead of the body's own
    guards -- so the over-limit request is rejected without touching ingest.
    """
    _register_and_login(client, "upload_ratelimit_user")
    summary = {"upload_id": "up_rl", "chunks": 1, "source": "note.txt"}
    with patch("upload_routes.ingest_upload", return_value=summary):
        statuses = [
            client.post(
                "/uploads",
                files={"file": ("note.txt", b"some intel", "text/plain")},
                data={"team_id": str(TRACKED_TEAM_ID)},
            ).status_code
            for _ in range(6)
        ]

    assert statuses[:5] == [201, 201, 201, 201, 201]
    assert statuses[5] == 429


def test_refresh_valid_token_is_202(client):
    """A valid refresh token starts a refresh and returns 202.

    _trigger_refresh is patched to report it started one (True); the endpoint
    maps that to 202. Patching the trigger -- not refresh_live_data -- means no
    real worker thread spawns, so this test cannot leak the in-progress flag
    into the direct single-flight test.
    """
    with patch("api.REFRESH_TOKEN", REFRESH_TOKEN_VALUE), patch(
        "api._trigger_refresh", return_value=True
    ):
        resp = client.post(
            "/refresh", headers={"X-Refresh-Token": REFRESH_TOKEN_VALUE}
        )
    assert resp.status_code == 202


def test_refresh_wrong_token_is_401(client):
    """A wrong token and a missing token are both 401, and start no refresh.

    refresh_live_data is patched (it exists before and after the rewrite) and
    asserted uncalled -- the token check short-circuits before any trigger, so
    an unauthorized caller never reaches the refresh path.
    """
    with patch("api.REFRESH_TOKEN", REFRESH_TOKEN_VALUE), patch(
        "api.refresh_live_data"
    ) as mock_refresh:
        wrong = client.post(
            "/refresh", headers={"X-Refresh-Token": "not-the-token"}
        )
        missing = client.post("/refresh")

    assert wrong.status_code == 401
    assert missing.status_code == 401
    mock_refresh.assert_not_called()


def test_refresh_already_running_is_409(client):
    """A valid trigger while a refresh is already in flight is 409.

    _trigger_refresh is patched to report False (already running); the endpoint
    must map that to 409 -- distinct from 202 (started) and 429 (too frequent).
    """
    with patch("api.REFRESH_TOKEN", REFRESH_TOKEN_VALUE), patch(
        "api._trigger_refresh", return_value=False
    ):
        resp = client.post(
            "/refresh", headers={"X-Refresh-Token": REFRESH_TOKEN_VALUE}
        )
    assert resp.status_code == 409


def test_refresh_rate_limit_returns_429_past_the_cap(client, rate_limited):
    """The fourth refresh inside the window is 429; the first three are 202.

    _trigger_refresh is patched to True so single-flight never interferes --
    this isolates the rate-limit decorator. The limiter check precedes the
    token check and the trigger, so the over-limit request is rejected first.
    """
    with patch("api.REFRESH_TOKEN", REFRESH_TOKEN_VALUE), patch(
        "api._trigger_refresh", return_value=True
    ):
        statuses = [
            client.post(
                "/refresh", headers={"X-Refresh-Token": REFRESH_TOKEN_VALUE}
            ).status_code
            for _ in range(4)
        ]

    assert statuses[:3] == [202, 202, 202]
    assert statuses[3] == 429


def test_trigger_refresh_is_single_flight():
    """The concurrency guard directly: a second trigger while one runs is False.

    refresh_live_data is patched to block on an Event so the first refresh stays
    in flight. The first _trigger_refresh starts it and returns True; the
    second, with the flag still set, declines and returns False. Releasing the
    worker lets it finish and clear the flag. This proves single-flight without
    the HTTP layer's threadpool timing, and asserts the flag resets on
    completion.
    """
    import api

    started = threading.Event()
    release = threading.Event()

    def blocking_refresh():
        started.set()
        release.wait(timeout=5)

    with patch("api.refresh_live_data", blocking_refresh):
        assert api._trigger_refresh() is True
        assert started.wait(timeout=2)
        assert api._trigger_refresh() is False
        release.set()

        deadline = time.monotonic() + 2
        while api._refresh_in_progress and time.monotonic() < deadline:
            time.sleep(0.01)
        assert api._refresh_in_progress is False