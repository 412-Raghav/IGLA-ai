"""Unit tests for chat_routes._serialize_messages -- pure, no DB, no network.

origins is persisted on the user turn but rendered on the assistant reply;
these lock the pairing (pass -> forward origins, reject -> [], dangling and
pre-origins rows -> no crash) without a DB round-trip. Endpoint wiring and
the JSONB round-trip are covered separately by the TestClient suite.
"""

from types import SimpleNamespace

from chat_routes import _serialize_messages


def _user(retrieval):
    return SimpleNamespace(
        id=1, role="user", content="q", created_at=None, retrieval=retrieval
    )


def _assistant(content="a"):
    return SimpleNamespace(
        id=2, role="assistant", content=content, created_at=None, retrieval=None
    )


def _pass_record(origins):
    return [{"attempt": 1, "gate": "pass", "origins": origins}]


def _reject_record(origins):
    # A rejected turn ends on attempt 2; origins may be non-empty (docs were
    # retrieved) but the gate still rejected them.
    return [
        {"attempt": 1, "gate": "reject", "origins": origins},
        {"attempt": 2, "gate": "reject", "origins": origins},
    ]


def test_pass_turn_with_upload_forwards_origins_to_assistant():
    out = _serialize_messages([_user(_pass_record(["general", "upload"])), _assistant()])
    assert out[1]["origins"] == ["general", "upload"]


def test_pass_turn_without_upload_forwards_general_only():
    out = _serialize_messages([_user(_pass_record(["general"])), _assistant()])
    assert out[1]["origins"] == ["general"]


def test_rejected_turn_gives_assistant_empty_origins():
    # Attempt 2 retrieved an upload doc, but a rejected turn must not advertise
    # it -- matches /ask returning origins=[] on the reject branch.
    out = _serialize_messages([_user(_reject_record(["upload"])), _assistant("out of scope")])
    assert out[1]["origins"] == []


def test_dangling_user_turn_does_not_crash_and_next_pair_is_correct():
    # A user turn whose generation failed leaves no assistant reply. The next
    # completed turn must pair with its OWN user turn, not the dangling one.
    out = _serialize_messages([
        _user(_pass_record(["upload"])),      # dangling: no assistant follows
        _user(_pass_record(["general"])),
        _assistant(),
    ])
    assert out[2]["origins"] == ["general"]


def test_missing_origins_key_defaults_to_empty():
    # Pre-9d rows may lack an origins key; reload must default to [] rather
    # than KeyError-500 the whole thread.
    out = _serialize_messages([_user([{"attempt": 1, "gate": "pass"}]), _assistant()])
    assert out[1]["origins"] == []