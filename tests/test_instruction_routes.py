"""HTTP-level integration tests for the per-team instruction routes.

Same harness as test_api_integration: the app driven through TestClient with
the savepoint-rolled-back session from conftest, so every request and
require_user share one connection inside an open transaction. No uvicorn, no
network, no Anthropic call.

These exercise what the service-layer suite cannot: the request path --
require_user, path/body validation, status codes -- and the one property that
only appears across the auth boundary. Per-user isolation: two accounts using
the SAME team_id see two independent instructions, because the row is keyed
(user_id, team_id) and user_id comes from the session, never the request.

_register_and_login/_login mirror the helpers in test_api_integration -- each
integration module stays self-contained rather than importing across test
modules; conftest supplies the fixtures, not these helpers.
"""

import pytest

from data.team_registry import TRACKED_TEAMS, TRACKED_TEAM_IDS
from instruction_service import MAX_INSTRUCTION_CHARS

pytestmark = pytest.mark.integration

# Derived from the registry SSOT, never hardcoded: if the registry changes,
# these follow. One past the highest id is untracked by construction.
TRACKED_TEAM_ID = TRACKED_TEAMS[0]["team_id"]
UNTRACKED_TEAM_ID = max(TRACKED_TEAM_IDS) + 1


def _register_and_login(client, username: str, password: str = "testpass123"):
    """Register a user and log them in; the client's cookie jar now carries
    their session. Returns the created user's id."""
    reg = client.post(
        "/auth/register", json={"username": username, "password": password}
    )
    assert reg.status_code == 201
    login = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200
    return reg.json()["id"]


def _login(client, username: str, password: str = "testpass123"):
    """Log in an already-registered user; their session replaces the cookie."""
    login = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200


def test_instructions_require_auth(client):
    """No session cookie: every instruction route is a clean 401, before any DB
    access -- require_user raises the moment the cookie is absent."""
    assert client.get(f"/instructions/{TRACKED_TEAM_ID}").status_code == 401
    assert (
        client.put(
            f"/instructions/{TRACKED_TEAM_ID}", json={"instructions_text": "x"}
        ).status_code
        == 401
    )
    assert client.delete(f"/instructions/{TRACKED_TEAM_ID}").status_code == 401


def test_get_returns_empty_when_unset(client):
    """A team with no instruction reads as an empty string -- not null, not a
    404 -- so the frontend binds it straight into a textarea."""
    _register_and_login(client, "instr_get_empty")
    resp = client.get(f"/instructions/{TRACKED_TEAM_ID}")
    assert resp.status_code == 200
    assert resp.json() == {"instructions_text": ""}


def test_put_then_get_round_trips(client):
    """PUT stores the instruction and echoes it back; a later GET returns it."""
    _register_and_login(client, "instr_put_get")
    text = "Punish their aggressive early rounds on defense."
    put = client.put(
        f"/instructions/{TRACKED_TEAM_ID}", json={"instructions_text": text}
    )
    assert put.status_code == 200
    assert put.json() == {"instructions_text": text}

    got = client.get(f"/instructions/{TRACKED_TEAM_ID}")
    assert got.status_code == 200
    assert got.json() == {"instructions_text": text}


def test_put_replaces_existing(client):
    """A second PUT replaces the first in place -- upsert, not append."""
    _register_and_login(client, "instr_replace")
    client.put(
        f"/instructions/{TRACKED_TEAM_ID}", json={"instructions_text": "first read"}
    )
    client.put(
        f"/instructions/{TRACKED_TEAM_ID}",
        json={"instructions_text": "revised read"},
    )
    got = client.get(f"/instructions/{TRACKED_TEAM_ID}")
    assert got.json() == {"instructions_text": "revised read"}


def test_put_strips_surrounding_whitespace(client):
    """Stored text is stripped: leading/trailing whitespace never persists."""
    _register_and_login(client, "instr_strip")
    client.put(
        f"/instructions/{TRACKED_TEAM_ID}",
        json={"instructions_text": "   hold the anchor   "},
    )
    got = client.get(f"/instructions/{TRACKED_TEAM_ID}")
    assert got.json() == {"instructions_text": "hold the anchor"}


def test_put_at_cap_after_strip_is_accepted(client):
    """Length is checked AFTER strip: exactly the cap in real characters plus
    trailing whitespace is accepted -- trailing newlines from a paste never
    trip a false rejection. Contrast test_put_over_cap_is_422."""
    _register_and_login(client, "instr_at_cap")
    at_cap = "x" * MAX_INSTRUCTION_CHARS
    resp = client.put(
        f"/instructions/{TRACKED_TEAM_ID}",
        json={"instructions_text": at_cap + "\n\n"},
    )
    assert resp.status_code == 200
    got = client.get(f"/instructions/{TRACKED_TEAM_ID}")
    assert got.json() == {"instructions_text": at_cap}


def test_put_over_cap_is_422(client):
    """One real character past the cap (nothing to strip away) is rejected with
    422 -- an instruction is load-bearing, so it is refused, not truncated."""
    _register_and_login(client, "instr_over_cap")
    resp = client.put(
        f"/instructions/{TRACKED_TEAM_ID}",
        json={"instructions_text": "x" * (MAX_INSTRUCTION_CHARS + 1)},
    )
    assert resp.status_code == 422


def test_put_blank_is_422(client):
    """A whitespace-only instruction is 422: PUT sets a real instruction,
    DELETE clears one. Storing "" would be a second representation of
    'no instruction' -- the design keeps exactly one, an absent row."""
    _register_and_login(client, "instr_blank")
    resp = client.put(
        f"/instructions/{TRACKED_TEAM_ID}", json={"instructions_text": "   "}
    )
    assert resp.status_code == 422


def test_delete_removes_instruction(client):
    """DELETE clears the instruction: 204, then GET reads empty again."""
    _register_and_login(client, "instr_delete")
    client.put(
        f"/instructions/{TRACKED_TEAM_ID}", json={"instructions_text": "temporary"}
    )
    assert client.delete(f"/instructions/{TRACKED_TEAM_ID}").status_code == 204
    assert client.get(f"/instructions/{TRACKED_TEAM_ID}").json() == {
        "instructions_text": ""
    }


def test_delete_absent_is_404(client):
    """Clearing an instruction that was never set is 404 -- mirrors the
    absent-conversation and absent-upload deletes, which the service's bool
    return already distinguishes."""
    _register_and_login(client, "instr_delete_absent")
    assert client.delete(f"/instructions/{TRACKED_TEAM_ID}").status_code == 404


def test_untracked_team_is_422(client):
    """An untracked team_id is 422 -- IGLA holds no intel for it and a thread
    can never anchor to it, so an instruction for it is dead data. Same
    tracked-team guard chat_routes and upload_routes enforce, applied to the
    path parameter here."""
    _register_and_login(client, "instr_untracked")
    assert client.get(f"/instructions/{UNTRACKED_TEAM_ID}").status_code == 422
    assert (
        client.put(
            f"/instructions/{UNTRACKED_TEAM_ID}", json={"instructions_text": "x"}
        ).status_code
        == 422
    )


def test_instruction_is_isolated_per_user(client):
    """The property that only appears across the auth boundary: two accounts
    using the SAME team_id have two independent instructions. user_id comes
    from the session, so one analyst's read on an opponent can never surface in
    another's -- the (user_id, team_id) key, not a where-filter, keeps them
    apart. The two users share one client; a login overwrites the cookie,
    exactly like a second browser.
    """
    _register_and_login(client, "instr_user_a")
    client.put(
        f"/instructions/{TRACKED_TEAM_ID}",
        json={"instructions_text": "A's read on this team"},
    )

    _register_and_login(client, "instr_user_b")
    # B sees nothing for the same team_id -- A's instruction is not B's.
    assert client.get(f"/instructions/{TRACKED_TEAM_ID}").json() == {
        "instructions_text": ""
    }
    client.put(
        f"/instructions/{TRACKED_TEAM_ID}",
        json={"instructions_text": "B's read on this team"},
    )

    # Back to A -- login, not re-register (the username is taken). A still sees
    # only A's; B's write never touched A's row.
    _login(client, "instr_user_a")
    assert client.get(f"/instructions/{TRACKED_TEAM_ID}").json() == {
        "instructions_text": "A's read on this team"
    }