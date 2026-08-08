# Phase 10 — Per-Team Instructions (Design Spec)

**Status:** approved, pre-implementation · **Branch:** `feat/9d-uploads` · **Storage:** Postgres

Per-team *standing instructions* — a small, analyst-authored system-prompt for one opponent,
**injected into every turn** of any chat anchored to that team. Keyed `(user_id, team_id)`,
stored in Postgres (not ChromaDB), never semantically retrieved.

This is the second of two Phase 10 sub-features. Notes (per-team upload grouping) is a
separate mini-scope tracked at the end of this doc.

---

## 1. Scope

**In scope**
- Author, store, edit, and delete a per-team instruction.
- Inject it into the system prompt on every turn, unconditionally.
- Per-turn audit of which instruction fired.

**Parked — candidates, deliberate adds only (not built now)**
- `/save`-from-chat: promote a user-selected line from a chat into the team's notes. Human picks; no extraction.
- Stale-strat nudge: prompt "still relevant?" on an instruction that has gone quiet; archive-on-confirm.
- Team-name fuzzy/alias matching (see §12).

**Rejected — do not reopen**
- **Auto-memory extraction.** An LLM deciding what is "important" and writing it into an always-injected
  store is an ungated generator on the highest-stakes path. A single over-generalization
  ("PRX *always* double-controllers") becomes permanent, injected every turn, poisoning every future
  answer — and nothing flags it. This violates the project's *court reporter, not novelist* principle
  (generation is separated from serving; a human review gate catches errors). Manual authoring is the
  **safe** version of memory, precisely because the failure mode of the automated version is compounding
  and silent.
- **Blanket autocorrect** on user input. A generic English corrector mangles domain vocabulary — agent
  names (Sova, Killjoy, Fade), map callouts ("A main", "heaven", "hookah"), and team names — silently
  rewriting the query. The downstream consumers (the embedder and Claude) are already typo-robust.
- **Instruction versioning / edit-history as primary storage.** See §3.1.

**Separate mini-scope (next, after this ships)**
- Notes: UI grouping of uploads by team + one team-axis isolation test + confirm `GET /uploads`
  returns `team_id` per row. No retrieval change (uploads are already team-filtered — see §11).

---

## 2. Why inject, not retrieve

A standing instruction must appear on **every** turn. Retrieval is ranked and lossy: a similarity gate
might not surface it. The correct tool for must-always-apply text is guaranteed injection, so the
instruction lives in Postgres and is read on a point lookup, never embedded into ChromaDB.

Corollary — **why Postgres, not a cached prompt block.** IGLA runs `claude-sonnet-4-6`, whose minimum
cacheable prompt length is 1,024 tokens. The composed system field (base ≈ 180 tokens + instruction)
lands around 250–400 tokens — below the floor — so a `cache_control` marker would be silently ignored.
Caching is a throughput optimization; a portfolio deployment with sporadic traffic against a ~5-minute
TTL has no throughput to optimize. Prompt caching is therefore deliberately **not** used, and the
system field is composed as a plain string (see §7).

---

## 3. Design decisions

### 3.1 Storage — single-row upsert

One row per `(user_id, team_id)` with a **unique constraint** on the pair; `PUT` replaces it in place
via `INSERT … ON CONFLICT DO UPDATE`.

- An instruction is a **current-state object**, not an event stream — it has exactly one true value at a
  time. Modeling it as an append log is a category error.
- The read is the hot path (every turn). Upsert makes it a point lookup guaranteed to return exactly one
  row: no `ORDER BY`, no "which is latest" tiebreaker. Append-history would put
  `ORDER BY created_at DESC LIMIT 1` on the hot path and re-import the frozen-`now()` tiebreaker hazard
  that `get_current_anchor` and `get_history` already carry comments about (Postgres freezes `now()` at
  transaction start, so rows written together share a `created_at`).
- The unique constraint makes "one live instruction per opponent" **impossible to violate** — enforced by
  the database, not by application discipline. Same principle as per-user upload collections.
- If a versioned history ever becomes a real feature, it arrives as a **separate** append-only revisions
  table keyed off this row — never by reshaping this one. Overloading the every-turn read to also be an
  archive is the worst of both.

### 3.2 Injection mechanism — string append (Option A)

`ask_igla` composes `system = SYSTEM_PROMPT + wrapper(instruction)` when an instruction is present.
Structured system blocks (Option B) were rejected: their only benefit is `cache_control`, which is inert
here (§2). Option A is the terminal-correct shape, not a placeholder. Absent instruction → `system` is
byte-identical to today.

### 3.3 Comparison turns — anchor-only

When a turn names 2+ teams, the anchor **holds** (9e semantics). Only the anchor team's instruction is
injected. Injecting multiple teams' guidance would splice contradictory standing instructions into one
system prompt, and the secondary team is often untracked. Header, retrieval, and instruction all read the
same `effective_team_id`, so they cannot disagree.

### 3.4 Over-cap — reject, do not truncate

`set_title_if_absent` truncates because a title is cosmetic. An instruction is load-bearing — silently
clipping it could drop the decisive line. So the API **rejects** an over-cap instruction with `422` and
tells the user, rather than truncating.

### 3.5 Audit — a key inside the existing JSONB

Which instruction fired is written into the user turn's existing `entity_scope` JSONB, not a new column —
no messages-table migration, and it is semantically correct ("how this turn was processed"). This is the
audit half of *opponent A's data provably cannot leak into opponent B*: every post-feature user row can
answer "which `(user, team)` guidance was applied."

---

## 4. Data model

New table `team_instructions`:

| column              | type            | notes                                              |
|---------------------|-----------------|----------------------------------------------------|
| `id`                | PK              |                                                    |
| `user_id`           | int, FK → users | NOT NULL                                            |
| `team_id`           | int             | NOT NULL — stored as **int** (exact-type lesson)   |
| `instructions_text` | `varchar(2000)` | NOT NULL                                            |
| `created_at`        | timestamptz     | default `now()`                                    |
| `updated_at`        | timestamptz     | `onupdate now()`                                   |

**Unique constraint:** `(user_id, team_id)`.

`team_id` is stored as `int` deliberately: ChromaDB where-filters (used on the notes side) are exact-type,
and a string `"624"` would silently never match an int `624`. Keeping the type consistent across the two
stores avoids a cross-store type mismatch.

---

## 5. Service layer — `instruction_service.py`

New module, parallel to `chat_service.py`; commits its own transactions.

```
MAX_INSTRUCTION_CHARS = 2000   # must match team_instructions.instructions_text varchar(2000)

get_instruction(user_id, team_id, db) -> str | None   # point lookup, the hot path
upsert_instruction(user_id, team_id, text, db)         # INSERT … ON CONFLICT DO UPDATE
delete_instruction(user_id, team_id, db) -> bool       # row removed → get returns None → nothing injected
```

`user_id` appears in every WHERE clause; a user only ever touches their own `(user_id, team_id)` rows.
The `MAX_INSTRUCTION_CHARS` constant mirrors the repo's `TITLE_MAX_LENGTH = 120` pattern (constant beside
the service, restating the column width with a "must match" comment).

---

## 6. API layer — `instruction_routes.py`

A **new leaf module** registered on the app, avoiding a re-open of the `api → routes → api` import cycle
broken in 9g.

| method | route                        | behavior                                                        |
|--------|------------------------------|-----------------------------------------------------------------|
| GET    | `/instructions/{team_id}`    | current text (or empty) for the authed user + team              |
| PUT    | `/instructions/{team_id}`    | `.strip()`, length-check (`422` if over cap), upsert, return    |
| DELETE | `/instructions/{team_id}`    | remove; `204`                                                   |

`user_id` is taken from the session, never from input (uploads rule). The length check runs **after**
`.strip()`, so trailing whitespace never trips a false rejection.

---

## 7. Injection seam (the core)

`ask_igla` gains one argument and does **not** fetch — generation stays a single job, per its own
docstring. The `/ask` call site fetches the instruction for `effective_team_id` (the resolved anchor) and
passes the text in.

```python
# main.py — module constant beside SYSTEM_PROMPT
_TEAM_GUIDANCE = (
    "\n\nSTANDING GUIDANCE from the analyst for this opponent. "
    "Apply it unless it conflicts with the instructions above:\n"
    "<team_guidance>\n{instructions}\n</team_guidance>"
)

def ask_igla(situation, context, history, team_instructions=""):
    ...
    system = SYSTEM_PROMPT
    if team_instructions:
        system += _TEAM_GUIDANCE.format(instructions=team_instructions)
    message = client.messages.create(
        model=MODEL_NAME, max_tokens=MAX_TOKENS,
        system=system,
        messages=[*history, {"role": "user", "content": augmented_message}],
    )
```

The wrapper subordinates the analyst's text to the base prompt's authority and fences it in a tag,
matching how `SYSTEM_PROMPT` already fences untrusted intel. The instruction is **trusted-to-self**: it is
the analyst's own guidance for their own team, and the `(user_id, team_id)` key confines its blast radius
to their own session. Absent instruction → `system` equals `SYSTEM_PROMPT` exactly → no behavioral change.

Exact call-site wiring (which handler, how `effective_team_id` is threaded) is confirmed against a
full-file read of the `/ask` handler at implementation time.

---

## 8. Observability

At the `/ask` call site, the `add_message(...)` that persists the **user** turn records which instruction
fired, inside the existing `entity_scope`:

```python
add_message(
    conversation_id, "user", situation, db,
    entity_scope={
        "team_ids": resolved_team_ids,               # existing — get_current_anchor reads this
        "instruction": {"team_id": T, "chars": N},   # new — or None when none fired
    },
    retrieval=retrieval_record,
)
```

The full instruction text is **not** duplicated per turn (it already lives in `team_instructions`); only
`{team_id, chars}` or `None`. `get_current_anchor` reads only `team_ids`, so the sibling key does not
disturb the anchor.

---

## 9. Tests (red-first)

| assertion                              | layer        |
|----------------------------------------|--------------|
| instruction present → in composed system | unit         |
| absent → system byte-identical to today  | unit         |
| wrong team → never injected              | unit         |
| comparison turn → anchor-only            | unit         |
| over-cap → `422`                         | integration  |
| second PUT replaces (one row remains)    | integration  |
| DELETE removes                           | integration  |
| user B cannot touch user A's row         | integration  |

Split by the existing marker convention: composition tests are unit (no `api.py` import graph); the
route/DB tests are integration (TestClient).

---

## 10. Migration

One Alembic migration: create `team_instructions` and its unique constraint. No change to the `messages`
table (audit rides in existing JSONB — §8).

---

## 11. Frontend

Per-team instruction editor: load via GET into a textarea, show the character cap live, save via PUT,
clear via DELETE. Minimal for now — markdown rendering, per-team accent, and other visual polish are
deferred to the project-wide polish pass, which runs once over the final UI shape.

---

## 12. Notes mini-scope (next)

Confirmed by reading `rag/uploads.py` and `rag/retriever.py`: uploads are tagged with `team_id` at ingest
(`ingest_upload` writes `"team_id": team_id`, int), and `retrieve_merged` filters the user's collection
with `where={"team_id": team_id}`. So the knowledge plane is **already** team-scoped — a note for one team
cannot surface in another team's turn. The isolation runs on two axes with two mechanisms:

- **User axis** (analyst vs analyst) — impossible-by-construction: only the caller's own collection is
  ever opened.
- **Team axis** (opponent vs opponent, same analyst) — enforced by the single `where={"team_id": team_id}`
  filter.

Notes work is therefore: UI grouping by team + **one team-axis isolation test** (a note for team A must
not appear in a team-B query for the same user — distinct from the user-vs-user test) + confirm
`GET /uploads` returns `team_id` per row. The team-axis test is what earns the word "provably" on that
axis, since a filter (unlike the user axis) can be forgotten in a refactor.

The related `team_registry` already carries an aliases field; if typo'd team names failing to move the
anchor becomes a real problem, fuzzy/alias matching there is the targeted fix — not blanket autocorrect.

---

## Appendix — placement at a glance

| decision                | lives in                                                                 | used as                                  |
|-------------------------|--------------------------------------------------------------------------|------------------------------------------|
| 2000-char cap           | constant in `instruction_service.py` · column `varchar(2000)` · FE counter | 3-layer: DB backstop · API gate · UX     |
| wrapper string          | constant in `main.py`, used in `ask_igla`                                | one line composing `system`              |
| reject over cap         | `PUT` handler in `instruction_routes.py`                                 | `422`, checked after `.strip()`          |
| audit                   | `add_message(entity_scope=…)` at the `/ask` site                        | key in existing JSONB — no migration     |
| anchor-only injection   | `/ask` call site (reads `effective_team_id`)                            | fetch one instruction, pass to `ask_igla`|