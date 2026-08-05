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
"""


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