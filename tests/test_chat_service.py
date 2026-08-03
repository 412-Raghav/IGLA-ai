"""Unit tests for the conversation service layer.

No HTTP, no LLM, no network. Every test runs against igla_test and rolls
back -- see conftest.

The load-bearing test is test_get_history_excludes_gate_rejected_pair.
It is design decision 2.6 made executable: a gate-rejected turn is
PERSISTED (that is the measurement Session 2 opens with) but never REPLAYED
into the prompt. If that test lies, Session 2 has no baseline.
"""

import pytest

import chat_service
from models import Message

PASS = [{"attempt": 1, "query": "q", "doc_ids": ["d1"], "best_distance": 0.83, "gate": "pass"}]
REJECT = [{"attempt": 1, "query": "q", "doc_ids": [], "best_distance": 0.91, "gate": "reject"}]


def test_create_conversation_returns_row_with_null_title(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    assert conv.id is not None
    assert conv.user_id == user.id
    assert conv.team_id == 624
    assert conv.title is None          # set on first /ask, not at creation
    assert conv.created_at is not None


def test_list_conversations_only_returns_own(db, user, other_user):
    chat_service.create_conversation(user.id, 624, db)
    chat_service.create_conversation(other_user.id, 17, db)
    mine = chat_service.list_conversations(user.id, db)
    assert len(mine) == 1
    assert mine[0].user_id == user.id


def test_get_conversation_returns_none_for_other_users_id(db, user, other_user):
    conv = chat_service.create_conversation(user.id, 624, db)
    assert chat_service.get_conversation(conv.id, other_user.id, db) is None
    assert chat_service.get_conversation(conv.id, user.id, db) is not None


def test_add_message_persists_entity_scope_and_retrieval(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    scope = {"team_ids": [624]}
    msg = chat_service.add_message(
        conv.id, "user", "How does SEN attack Lotus?", db,
        entity_scope=scope, retrieval=PASS,
    )
    db.expire_all()                    # force a real read back from Postgres
    fetched = db.get(Message, msg.id)
    assert fetched.entity_scope == scope
    assert fetched.retrieval[0]["best_distance"] == 0.83
    assert fetched.retrieval[0]["gate"] == "pass"


def test_get_history_excludes_gate_rejected_pair(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "How does SEN attack Lotus?", db, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "SEN leans on early C-lobby control.", db)
    chat_service.add_message(conv.id, "user", "why does that work?", db, retrieval=REJECT)
    chat_service.add_message(conv.id, "assistant", "That doesn't look like a Valorant question.", db)
    chat_service.add_message(conv.id, "user", "What about Haven?", db, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "Haven is a different problem.", db)

    history = chat_service.get_history(conv.id, db)
    contents = [m["content"] for m in history]

    assert "why does that work?" not in contents
    assert "That doesn't look like a Valorant question." not in contents
    assert len(history) == 4           # both passing pairs, neither rejected turn

    # all six rows still exist -- persisted, just not replayed
    assert db.query(Message).filter_by(conversation_id=conv.id).count() == 6


def test_get_history_returns_anthropic_message_format(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "q", db, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a", db)
    # exact equality: no extra keys may leak into the Anthropic payload
    assert chat_service.get_history(conv.id, db) == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]


def test_get_history_excludes_dangling_user_turn(db, user):
    """A gate-passing user turn with no assistant reply is what a 502 leaves
    behind. Replaying it would put two consecutive user messages in the
    Anthropic array.
    """
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "q1", db, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a1", db)
    chat_service.add_message(conv.id, "user", "q2-claude-died", db, retrieval=PASS)
    chat_service.add_message(conv.id, "user", "q3", db, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a3", db)

    history = chat_service.get_history(conv.id, db)

    assert [m["content"] for m in history] == ["q1", "a1", "q3", "a3"]
    # all five rows persist -- the dangling turn is recorded, just not replayed
    assert db.query(Message).filter_by(conversation_id=conv.id).count() == 5


def test_get_history_excludes_trailing_dangling_user_turn(db, user):
    """The production shape: Claude dies on the newest turn, then the user
    asks again. get_history runs BEFORE the new turn is written, so the failed
    turn is the last row in the thread when history is assembled.
    """
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "q1", db, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a1", db)
    chat_service.add_message(conv.id, "user", "q2-claude-died", db, retrieval=PASS)

    assert [m["content"] for m in chat_service.get_history(conv.id, db)] == ["q1", "a1"]


def test_delete_conversation_cascades_messages(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "q", db, retrieval=PASS)
    conv_id = conv.id
    assert chat_service.delete_conversation(conv_id, user.id, db) is True
    assert db.query(Message).filter_by(conversation_id=conv_id).count() == 0


def test_delete_conversation_returns_false_for_other_user(db, user, other_user):
    conv = chat_service.create_conversation(user.id, 624, db)
    assert chat_service.delete_conversation(conv.id, other_user.id, db) is False
    assert chat_service.get_conversation(conv.id, user.id, db) is not None


def test_set_title_if_absent_does_not_overwrite(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.set_title_if_absent(conv.id, "How does SEN attack Lotus?", db)
    db.refresh(conv)
    assert conv.title == "How does SEN attack Lotus?"
    chat_service.set_title_if_absent(conv.id, "A completely different question", db)
    db.refresh(conv)
    assert conv.title == "How does SEN attack Lotus?"


def test_set_title_truncates_long_message(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.set_title_if_absent(conv.id, "x" * 500, db)
    db.refresh(conv)
    assert len(conv.title) <= 120      # String(120) -- untruncated raises DataError


def test_current_anchor_seeds_from_birth_when_no_turns(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    assert chat_service.get_current_anchor(conv.id, 624, db) == 624


def test_current_anchor_reads_last_user_turn_scope(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(
        conv.id, "user", "now compare with Global Esports", db,
        entity_scope={"team_ids": [918]}, retrieval=PASS,
    )
    chat_service.add_message(conv.id, "assistant", "GE breakdown.", db)
    assert chat_service.get_current_anchor(conv.id, 624, db) == 918


def test_current_anchor_follows_most_recent_of_several(db, user):
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "PRX?", db, entity_scope={"team_ids": [624]}, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a", db)
    chat_service.add_message(conv.id, "user", "now GE", db, entity_scope={"team_ids": [918]}, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a", db)
    # last-named wins; the id tiebreaker orders rows sharing a frozen created_at
    assert chat_service.get_current_anchor(conv.id, 624, db) == 918


def test_rejected_turn_still_moves_anchor(db, user):
    """A turn that named a team moved the anchor whether or not it passed the
    gate -- get_current_anchor reads the last user row regardless of outcome.
    """
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(
        conv.id, "user", "now compare with Global Esports", db,
        entity_scope={"team_ids": [918]}, retrieval=REJECT,
    )
    # rejected turn has no assistant reply, but the anchor still moved
    assert chat_service.get_current_anchor(conv.id, 624, db) == 918


def test_current_anchor_falls_back_when_scope_missing(db, user):
    """Defensive: a user row without a usable team_ids falls back to birth
    rather than raising -- an unpopulated scope must not break the next turn.
    """
    conv = chat_service.create_conversation(user.id, 624, db)
    chat_service.add_message(conv.id, "user", "q", db, entity_scope=None, retrieval=PASS)
    chat_service.add_message(conv.id, "assistant", "a", db)
    assert chat_service.get_current_anchor(conv.id, 624, db) == 624