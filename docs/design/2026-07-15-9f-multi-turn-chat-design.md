# 9f — Multi-turn chat (Session 1: persistence + plumbing)

**Date:** 2026-07-15
**Status:** Design approved, not implemented
**Scope:** Session 1 of 2. Session 2 (gate-on-follow-up redesign) is specified separately.

---

## 1. Problem

`/ask` is one-shot. Each call is an independent question with no memory of the last.
Real tactical prep is conversational: an IGL asks a question, reads the answer, and
pushes on it.

The messages-array plumbing is the easy 20%. The hard part — and the reason this is a
phase rather than a bolt-on — is that **conversational follow-ups fight the scope gate.**

- `"How do GE defend Haven A?"` — team + map + site keywords. Retrieves well. Gate passes.
- `"Why does that work?"` — no team, no map, no player. High `best_distance`. **Gate rejects
  it as off-topic**, even though it is obviously in context.

Session 1 builds the persistence and plumbing, and **reproduces that failure with the
evidence recorded**. Session 2 fixes it.

---

## 2. Decisions

Six decisions were made before any code. Each is recorded with its rejected alternatives,
because the reasoning is the portfolio value.

### 2.1 Retrieve every turn (rejected: retrieve once, coast on context)

Coasting appears to solve gate-on-follow-up for free: never retrieve on the follow-up, so
the gate never fires. It fails the moment the user pivots. `"What about when they face
MIBR?"` is a new information need; turn 1's context does not contain it. Claude with no
relevant retrieved facts does not stay silent — it invents tactical intel. That is the
generation-discipline failure ("court reporter, not novelist") this codebase exists to
prevent.

You cannot tell in advance whether a follow-up is a clarification of retrieved context or
a fresh need. A coast policy is therefore a standing bet that the model already has what
it needs, and it hallucinates every time that bet loses.

Also rejected: **retrieval-as-a-tool** (hand Claude a tool and let it decide when to call
it). Less predictable, harder to eval deterministically against `retrieve_ranked`, and it
reintroduces the hallucinate-when-it-skips risk. Documented as a v2 alternative, not built.

### 2.2 The gate becomes a router, not a wall (Session 2)

Today: pass → answer, fail → reject. Terminal.

Session 2: pass → answer, fail → **treat the failure as the signal that the message is
reference-dependent.** Contextualize it into a standalone question (`"why does that work?"`
→ `"why does Gen.G's Haven A defense work?"`), retry retrieval, and reject only if the
rewritten query also fails.

Rejection becomes a branch, not a dead end. **Session 1 does not implement this.** Session 1
leaves the gate terminal and measures how often it is hit.

### 2.3 Server owns the thread (rejected: client ships the messages array)

Client sends `{conversation_id, message}`. Server loads history from Postgres, assembles the
array, retrieves, calls Claude, persists both turns, returns the answer.

Rejected `client ships the array` because:

1. It contradicts the 9-prereq decision. Server-side sessions were chosen over JWT because
   server-owned state is the boring-correct call at single-replica scale. Client-owned
   conversation state argues the opposite side of the same principle in one codebase.
2. **It lets the client author the assistant's mouth.** A buggy or malicious client can send
   a forged assistant turn (`"you previously said GE runs a double-controller comp"`) and
   Claude will build on it. That is prompt injection through our own API contract.
3. The recent-chats panel needs persistence anyway. Client-ships means building persistence
   *and* a wire format — paying twice.

Accepted cost: a DB read per turn to assemble the array, plus conversation lifecycle
management.

### 2.4 Scope = thread anchor + per-turn set (rejected: accumulating set; turn-level only)

**Rejected — thread-level accumulating set.** Scope is not monotonic; it moves:

```
1. "How does SEN attack Lotus?"        -> {SEN}
2. "Why does that work?"               -> {SEN}
3. "Compare with GE"                   -> {SEN, GE}
4. "Back to SEN - what about B site?"  -> {SEN, GE}   <- wrong
```

An accumulating set never lets go. GE's docs sit in the candidate set for the rest of the
thread, diluting every ranking. This directly worsens the already-filed mis-rank finding
(`scope:"general"` curated docs out-ranking team-specific stat docs via the `$or` filter) —
a wider candidate set makes it measurably worse.

**Rejected — turn-level only.** No default for turn N, and nothing for the sidebar to
render. A thread with no anchor has no identity.

**Accepted — anchor + per-turn:**

- `conversations.team_id` — the dropdown pick. The thread's home team. Immutable. Renders in
  the sidebar; is the fallback for every turn.
- `messages.entity_scope` (JSONB) — what this turn actually retrieved against.

Turn N's scope = `anchor ∪ extract(message_N)`.

**In Session 1, `extract()` returns nothing.** Every turn's scope is the anchor alone, so
behavior is identical to today's `/ask`. The schema is already correct; Session 2 fills the
stub with **zero migration**.

JSONB over `INTEGER[]` because entity scope will grow — Session 2 adds players and maps, 9e
adds focus instructions. No referential integrity is given up, because `team_registry.py` is
a Python SSOT, not a table; there is no FK to lose. Validation stays at the API boundary
(Pydantic `field_validator` against the registry).

### 2.5 Persist dialogue + retrieval metadata; never replay context (rejected: dialogue only; replay context)

**Rejected — replay retrieved context into the array.** Appears to mitigate the Session 2
problem for free (turn 1's docs still in view when turn 2's retrieval is thin). It does not:
**the gate fires upstream of generation.** Turn 2 is rejected before the prompt is ever
assembled. Replayed context cannot rescue a turn that never reaches Claude. Everything else
is pure cost:

- Accumulation: ~1.5k tokens of docs per turn; a 20-turn thread carries ~30k tokens of
  largely duplicate documents.
- It is the scope leak relocated. Decision 2.4 exists to keep GE's docs out of turn 8's SEN
  question. Replay lets them in through the context window instead of the candidate set.
- Security: every replayed `<untrusted_data>` block is a live prompt-injection surface for
  the life of the thread. One poisoned doc on turn 1 contaminates every later turn.
  Fresh-context-only caps the blast radius at one turn.

**Rejected — dialogue only.** Session 2's entire job is fixing gate-on-follow-up, and you
cannot fix what you have not measured. Without the gate decision and `best_distance` recorded
per turn, Session 2 opens blind: no baseline, no before/after, no number for the README.
Dialogue-only is **eval-vs-prod gap #2 reappearing on a new surface** — a rank-1 hit that
production rejects, invisible to every owned metric. That bug is already filed once; do not
ship it twice.

**Accepted — persist both, replay neither.** Claude sees dialogue history plus turn N's
fresh context. The retrieval record lives in Postgres.

**Known limitation of this choice:** turn 2 is not fact-less — Claude's own turn-1 answer is
in the array and carries facts as prose. But it is a lossy, unverified paraphrase: only what
got surfaced, filtered through generation. If turn 1 drifted, turn 2 builds on the drift.
This is an argument *for* Session 2 (which re-grounds on real documents) rather than against
this decision.

### 2.6 Rejected turns persist but never replay

A gate-rejected exchange is written to Postgres (that is the measurement) but **excluded from
the history array on subsequent turns.** Two reasons:

1. A dangling user message with no real answer is noise.
2. The load-bearing one: replaying `"That doesn't look like a Valorant question"` into the
   array teaches Claude by example that rejection is a normal response shape. In-context
   pattern-following would make the gate problem *worse*.

Persist it, render it in the thread, never feed it back. No extra column: eligibility derives
from `retrieval.gate == "pass"` on the user row.

---

## 3. Architecture

### 3.1 New and changed files

Following the patterns already established by the 9-prereq:

| File | Status | Purpose |
|---|---|---|
| `chat_service.py` | new | conversation CRUD + message persistence. Mirrors `session_service.py`. |
| `chat_routes.py` | new | APIRouter for `/conversations`. Mirrors `auth_routes.py`. |
| `models.py` | changed | `Conversation` + `Message` ORM models |
| `alembic/versions/<new>` | new | one migration |
| `api.py` | changed | `/ask` signature, mount `chat_router` |
| `main.py` | changed | `ask_igla` signature (history + entity_scope replace team_id) |
| `index.html` | changed | sidebar, thread rendering, three-state panel logic |

`/ask` **stays in `api.py`.** It carries slowapi wiring that requires `request: Request` first
by name; moving it is unrelated risk on a session already touching six files. Filed as
possible later cleanup.

Full-file review of `main.py`, `api.py`, and `index.html` is required before writing against
them — real signatures and contracts, not guesses.

### 3.2 Schema

```sql
conversations
  id            UUID PK              -- not serial: sequential IDs invite IDOR
  user_id       FK users.id ON DELETE CASCADE, indexed
  team_id       INTEGER              -- the anchor (decision 2.4)
  title         VARCHAR(120)         -- first ~60 chars of first user message
  created_at    TIMESTAMPTZ server_default now()
  updated_at    TIMESTAMPTZ          -- sidebar ordering

messages
  id              BIGSERIAL PK
  conversation_id FK conversations.id ON DELETE CASCADE, indexed
  role            VARCHAR(16)        -- 'user' | 'assistant'
  content         TEXT
  entity_scope    JSONB NULL         -- input to retrieval (decision 2.4)
  retrieval       JSONB NULL         -- output of retrieval (decision 2.5)
  created_at      TIMESTAMPTZ server_default now()
```

Indexes: `(conversation_id, created_at)` for thread rendering;
`(user_id, updated_at DESC)` for the sidebar.

Both JSONB columns sit on the **user** row — they describe the processing of that message.
The assistant row holds only resulting text. A gate rejection therefore reads cleanly: user
row says *scoped to SEN, best_distance 0.91, gate rejected*; assistant row holds the
rejection message.

`retrieval` is **a list from day one**, length 1 in Session 1:

```json
[{"attempt": 1, "query": "...", "doc_ids": ["..."], "best_distance": 0.83, "gate": "pass"}]
```

Session 2's retrieve → reject → rewrite → retrieve appends attempt 2. No migration.

`title` is the truncated first user message. An LLM-generated title would cost Anthropic
tokens on every new thread — a bad trade against the zero-token-refresh discipline.
Upgradeable later.

### 3.3 Request flow (`POST /ask`)

```
POST /ask {conversation_id, message}
  |
  +-- require_user                        -> 401 if no session
  +-- load conversation, verify ownership -> 404 if not this user's
  +-- entity_scope = anchor U extract(message)   [Session 1: extract() -> {}]
  +-- retrieve_ranked(message, entity_scope)
  +-- persist user row (entity_scope + retrieval record)   <- one write, before Claude
  +-- gate check on best_distance
  |     |
  |     +-- REJECT: persist assistant row (rejection text). No Claude call. Return.
  |     |
  |     +-- PASS: load history (gate=="pass" pairs only)
  |               assemble array + fresh context
  |               call Claude
  |               persist assistant row
  |               touch conversations.updated_at
  |               Return.
```

**The user row is persisted after retrieval but before the Claude call.** Retrieval always
precedes generation, so one write captures both `entity_scope` and the full `retrieval`
record. If instead both rows were written after the LLM returned, every failed call would
vanish — and its retrieval record with it. Same principle as persisting rejections: the
record of what happened survives the thing not working.

### 3.4 API contracts

All five guarded by `require_user`.

| Endpoint | Body | Returns |
|---|---|---|
| `POST /conversations` | `{team_id}` | `{id, team_id, title, created_at}` |
| `GET /conversations` | — | list for sidebar, `updated_at DESC` |
| `GET /conversations/{id}` | — | thread + messages; 404 if not yours |
| `POST /ask` | `{conversation_id, message}` | `{answer, conversation_id, message_id}` |
| `DELETE /conversations/{id}` | — | 204 |

`team_id` moves from `/ask` to `POST /conversations`. It is asked once, at thread birth.

**Contract change consequence:** this is the second change to `/ask` in three days (the auth
guard was the first). The `docs/samples/` evidence is stale again — the captured samples show
an anonymous, `team_id`-taking `/ask` that no longer exists. Re-capture belongs with the
README showcase pass. Not blocking; recorded as debt.

### 3.5 Frontend

`index.html` gains a third state. The current binary (`showLogin()` / `showApp()`) becomes
three-way:

```
Login panel  --(/auth/me 200)-->  No thread open  --(select/create)-->  Thread open
     ^                                  |                                    |
     +----------------(logout or any 401)-----------------------------------+
```

- **New thread** is where the team dropdown now lives. It stops being furniture on the main
  screen and becomes part of thread creation.
- `checkAuth()` on load must answer two questions, not one: am I logged in, and do I have a
  thread open?
- The 401 bounce needs no change — every new endpoint 401s identically and `showLogin()` is
  already the handler.

Carried over unchanged: no `credentials:"include"` anywhere (same-origin; the cookie rides by
default), `errorText()` still handles both FastAPI detail shapes (422 array vs raised
`HTTPException` string), `/` still serves `index.html` via `FileResponse`.

---

## 4. Error handling

| Failure | Response | Rationale |
|---|---|---|
| No session | 401 | existing `require_user` |
| `conversation_id` not yours / absent | **404, not 403** | 403 confirms the row exists, leaking that someone else owns that thread |
| Bad `team_id` at creation | 422 | Pydantic `field_validator` against `team_registry` at the boundary. A `ValueError` deep in `ask_igla` turns a client error into a 500. |
| Claude call fails | 502 | user row already persisted with its retrieval record; no assistant row. Turn stays auditable. |
| Gate rejects | 200 | it is a valid answer, not an error. Recorded, rendered, not replayed. |

---

## 5. Verification

**All checks run on `uvicorn api:app` with no `--reload`.** One process, zero ambiguity about
which bytes are live. (Yesterday's stale-worker scare: `--reload` thrashed, a worker booted
between two saves and served pre-guard bytecode, and an unauthenticated `/ask` returned 200
with a full tactical read. The code was correct; the process was not.)

| # | Check | Expected | Anthropic tokens |
|---|---|---|---|
| 1 | `alembic upgrade head`; `\dt` and `\d conversations` | both tables; server_defaults present. **Read the migration before running it** — autogenerate is a tool, not an oracle. | 0 |
| 2 | **Audit Chroma's stored `team_id` type** via `collection.get(limit=1)` | know whether it is `624` or `"624"` before writing any where-filter | 0 |
| 3 | `POST /conversations`, blank cookie | 401 | 0 |
| 4 | `POST /conversations {team_id: 99999}` | 422 from the validator, not a 500 | 0 |
| 5 | User A's `/ask` with User B's `conversation_id` | **404** — not 403, not 200 | 0 |
| 6 | Turn 1: "How does SEN attack Lotus?" | answer; 2 rows; user row `retrieval.gate == "pass"` | yes |
| 7 | Turn 2: "why does that work?" | **gate reject, persisted, `best_distance` recorded** | **0** |
| 8 | Turn 3: a well-formed question | rejected pair absent from the assembled array | yes |
| 9 | `DELETE FROM sessions` mid-thread, then `/ask` | 401 | 0 |
| 10 | `DELETE /conversations/{id}` | messages rows cascade-gone | 0 |
| 11 | Frontend: sidebar renders, thread loads, new-thread flow | all three states reachable | yes (1 call) |

**Check 2 is the landmine.** Postgres holds `team_id` as an int; Chroma may hold `"624"` as a
string. A type-mismatched where-filter returns **zero docs, silently, with no error**. That
boundary is now crossed on every turn. Audit the stored metadata type; do not assume it.

**Check 5 is the one people skip.** Two users, cross the streams. It is the reason
`conversations.id` is a UUID and the reason the response is 404.

**Check 7 is a PASS, not a failure.** The test asserts the gap exists and is measured. The
gate fires upstream of Claude, so this failure costs zero tokens to reproduce — Session 2
inherits an infinitely repeatable, free test case.

---

## 6. Known gaps, shipped deliberately

| Gap | Resolution |
|---|---|
| Conversational follow-ups die at the gate | Session 2 — the point of the phase |
| `"compare with GE"` does not work; cross-team scope needs extraction | Session 2 fills the `extract()` stub; the column already exists |
| Turn 2 grounds on Claude's paraphrase, not re-retrieved docs | Session 2's re-retrieval |
| No compaction; long threads grow the array unboundedly | cost, not correctness. Later. |
| `docs/samples/` evidence stale | re-capture with the README showcase pass |
| `secure=False` cookie | local-dev only; `config.COOKIE_SECURE` when it matters |

**Explicitly out of scope for Session 1:** gate logic, `SCOPE_THRESHOLD`, the embedder,
`retrieve_ranked` internals. **Zero changes to the retrieval path.** If retrieval behaves
differently after Session 1, it is a plumbing bug — not a retrieval change — because there
was no retrieval change. That is what makes Session 2's before/after a measurement rather
than an argument.

**Untouched, unchanged:** the four held feature-branch files (`rag/embedder.py` mpnet swap,
`evals/calibrate_threshold.py`, `evals/embedding_ab.py`, `.gitignore`). Push remains held;
main still auto-deploys.

---

## 7. Rejected as out of scope (with reasoning, for the README)

**GraphRAG / knowledge-graph memory.** Real technique, correct instinct, wrong phase. Its
value is multi-hop relational reasoning; IGLA's corpus is 77 mostly-flat stat docs with a
three-level entity graph (team → player → agent/role) and near-universally single-hop
queries. Decisive objection: **GraphRAG requires an LLM pass over every document at ingest to
extract entities and relations, which breaks the zero-Anthropic-token daily refresh** — a
documented, owned cost property. Trading it away to answer questions nobody asks is a bad
trade.

The cheap version that captures the load-bearing value is already in this design:
`messages.entity_scope` is a per-turn entity index. It gives thread scope accumulation,
pronoun-resolution material for Session 2's rewriter, and a compaction anchor — in one
column. GraphRAG becomes a later upgrade rather than a rewrite. Filed as a Phase 10
candidate.

*(Decision-trees / random-forests / GNNs were considered and are not applicable: the first
two are supervised classifiers over tabular features with no relationship to conversation
memory; the third needs a graph, a training objective, and labeled data, none of which exist
here.)*

**LLM gateway / model routing.** Mature product category (LiteLLM self-hosted, OpenRouter
hosted, Portkey enterprise, Martian/RouteLLM for learned routing). Not built, because IGLA
has one provider — a gateway in front of one provider buys observability, not routing. **The
seam already exists: `llm.py`'s shared client is exactly the abstraction boundary a router
slots into.** Keep it clean; note it as v2 architecture.

The decisive objection is IGLA-specific: **a silent fallback destroys the eval baseline.** The
golden set measures Claude. If production silently routes to a local model on token
exhaustion, the eval scores a fiction while production serves something else — eval-vs-prod
gap, again. If ever built, the model must be a logged, measured variable, not invisible
plumbing.

**Correction to the VOD premise** (recorded because the reasoning generalizes): VOD → text is
cheap — a 40-minute transcript is ~6-8k tokens. The expensive part of VOD is **vision**:
frame-by-frame position, utility, and timing extraction. A local *text* LLM does not help with
that at all, so provider-fallback does not address the actual cost driver. The real lever is
**tiered processing**: sparse frame sampling, cheap local passes first (scoreboard OCR,
minimap extraction, Whisper on comms — all free and local), escalating to a frontier model
only on rounds that matter. A pipeline decision, not a routing decision.

---

## 8. Session 2 (specified separately, not built here)

The gate-as-router redesign: on gate failure, contextualize the message into a standalone
question using the thread's dialogue and `entity_scope` history, re-retrieve, and reject only
if the rewritten query also fails. Fills `extract()`, appends attempt 2 to the `retrieval`
list, and deletes the rejection path for in-context follow-ups.

Session 1's check 7 is Session 2's baseline.
