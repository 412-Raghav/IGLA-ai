"""Per-team instruction service: one standing instruction per (user, team).

Operations on the `team_instructions` table:
  get_instruction    -- the current instruction for one (user, team), or None.
  upsert_instruction -- write it, replacing any existing one in place.
  delete_instruction -- remove it; False if there was nothing to remove.

STORAGE IS SINGLE-ROW UPSERT, not append-history. A team instruction is a
current-state object -- "the analyst's live read on this opponent" -- with
exactly one true value at a time. The UNIQUE (user_id, team_id) constraint
enforces that in the schema, so get_instruction is a point lookup that cannot
return two rows: no ORDER BY, no "which row is latest" tiebreaker, and none of
the frozen-now() hazard get_current_anchor and get_history carry. A second
write REPLACES rather than appends. See docs/phase-10-instructions-design.md.

OWNERSHIP: user_id is in every WHERE clause. A caller only ever reads or writes
its own (user_id, team_id) rows; there is no code path to another user's.

INJECTION, NOT RETRIEVAL: this instruction is injected into the system prompt
every turn (see main.ask_igla), never embedded into ChromaDB. Retrieval is
ranked and lossy; must-always-apply text belongs on a guaranteed point read.

Transactions: this layer commits its own, matching chat_service and
session_service.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from models import TeamInstruction

MAX_INSTRUCTION_CHARS = 2000  # must match TeamInstruction.instructions_text String(2000)


def get_instruction(user_id: int, team_id: int, db: DbSession) -> str | None:
    """The current instruction text for one (user, team), or None if unset.

    The hot path: read every turn before generation. None is the zero-
    instruction fast path -- the caller injects nothing and the system prompt
    is byte-identical to one with no instruction. The UNIQUE (user_id, team_id)
    constraint guarantees at most one row, so scalar_one_or_none cannot raise
    on a duplicate.
    """
    return db.execute(
        select(TeamInstruction.instructions_text).where(
            TeamInstruction.user_id == user_id,
            TeamInstruction.team_id == team_id,
        )
    ).scalar_one_or_none()


def upsert_instruction(
    user_id: int, team_id: int, text: str, db: DbSession
) -> None:
    """Set the instruction for one (user, team), replacing any existing one.

    A single INSERT ... ON CONFLICT DO UPDATE: one statement, one row touched,
    last-write-wins. This is the upsert the storage decision turns on -- the
    write can never create a second row for the same pair, so 'append' is not
    representable, and the every-turn read stays unambiguous.

    updated_at is set explicitly in the DO UPDATE clause because onupdate=
    func.now() fires on an ORM UPDATE, not on this Core-level ON CONFLICT path;
    without it, a replaced instruction would keep its original updated_at.

    Length is NOT checked here -- the API boundary rejects over-length input
    (an instruction is load-bearing; it is refused, not truncated). This
    service is the persistence seam and stays policy-free, so an internal
    caller cannot be silently truncated either.
    """
    stmt = insert(TeamInstruction).values(
        user_id=user_id, team_id=team_id, instructions_text=text
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_team_instructions_user_team",
        set_={"instructions_text": text, "updated_at": func.now()},
    )
    db.execute(stmt)
    db.commit()


def delete_instruction(user_id: int, team_id: int, db: DbSession) -> bool:
    """Remove the instruction for one (user, team). False if none existed.

    Deleting the row (rather than storing "") means get_instruction returns
    None and the injection path is skipped entirely -- one representation of
    'no instruction', not two. The bool mirrors delete_conversation: it reports
    whether a row was actually removed, so the route can answer honestly.
    """
    result = db.execute(
        delete(TeamInstruction).where(
            TeamInstruction.user_id == user_id,
            TeamInstruction.team_id == team_id,
        )
    )
    db.commit()
    return result.rowcount > 0