"""Unit tests for the per-team instruction service.

No HTTP, no LLM, no network. Every test runs against igla_test and rolls
back -- see conftest.

The load-bearing test is test_upsert_replaces_in_place_keeps_one_row. It is
the storage decision made executable: a team instruction is a current-state
object, not an event log, so a second write REPLACES rather than appends, and
the unique (user_id, team_id) constraint makes a second row impossible by
construction. If that test lies, the every-turn read can serve a stale version
and the isolation story loses the word 'provably'.
"""

import instruction_service
from models import TeamInstruction

# 624 = Paper Rex, 918 = Global Esports -- the ids used across the suite.


def test_max_instruction_chars_is_2000():
    # single source of the cap; the PUT handler imports this same constant
    assert instruction_service.MAX_INSTRUCTION_CHARS == 2000


def test_get_instruction_returns_none_when_absent(db, user):
    # the zero-instruction fast path: nothing stored -> nothing injected
    assert instruction_service.get_instruction(user.id, 624, db) is None


def test_upsert_creates_row_then_get_returns_text(db, user):
    instruction_service.upsert_instruction(
        user.id, 624, "Punish their aggressive early rounds.", db
    )
    assert instruction_service.get_instruction(user.id, 624, db) == (
        "Punish their aggressive early rounds."
    )


def test_upsert_replaces_in_place_keeps_one_row(db, user):
    instruction_service.upsert_instruction(user.id, 624, "first read", db)
    instruction_service.upsert_instruction(user.id, 624, "revised read", db)
    db.expire_all()                    # force a real read back from Postgres
    assert instruction_service.get_instruction(user.id, 624, db) == "revised read"
    # one row, not two -- unique (user_id, team_id) makes append impossible
    assert (
        db.query(TeamInstruction)
        .filter_by(user_id=user.id, team_id=624)
        .count()
        == 1
    )


def test_instruction_is_scoped_by_team(db, user):
    instruction_service.upsert_instruction(user.id, 624, "PRX plan", db)
    instruction_service.upsert_instruction(user.id, 918, "GE plan", db)
    # same user, two teams -> two independent instructions
    assert instruction_service.get_instruction(user.id, 624, db) == "PRX plan"
    assert instruction_service.get_instruction(user.id, 918, db) == "GE plan"


def test_instruction_is_scoped_by_user(db, user, other_user):
    instruction_service.upsert_instruction(user.id, 624, "my PRX read", db)
    instruction_service.upsert_instruction(other_user.id, 624, "their PRX read", db)
    # same team_id, two users -> the (user_id, team_id) key keeps them apart
    assert instruction_service.get_instruction(user.id, 624, db) == "my PRX read"
    assert instruction_service.get_instruction(other_user.id, 624, db) == "their PRX read"


def test_delete_removes_row_and_get_returns_none(db, user):
    instruction_service.upsert_instruction(user.id, 624, "temporary", db)
    assert instruction_service.delete_instruction(user.id, 624, db) is True
    assert instruction_service.get_instruction(user.id, 624, db) is None
    assert (
        db.query(TeamInstruction)
        .filter_by(user_id=user.id, team_id=624)
        .count()
        == 0
    )


def test_delete_returns_false_when_absent(db, user):
    # nothing to delete -> False, mirroring delete_conversation's contract
    assert instruction_service.delete_instruction(user.id, 624, db) is False