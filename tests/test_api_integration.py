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

from unittest.mock import patch

import anthropic

from data.team_registry import TRACKED_TEAMS, TRACKED_TEAM_IDS
from main import REJECTION_MESSAGE
from rag.retriever import format_context

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