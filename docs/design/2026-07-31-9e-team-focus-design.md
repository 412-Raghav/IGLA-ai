# 9e — Team focus (mid-thread re-scope)

Date: 2026-07-31
Status: design approved — MOVE chosen. Build not started.

## Problem
`entity_scope` is always the thread's BIRTH anchor (`conversations.team_id`, set
at creation). A turn naming a different tracked team ("now compare with GE",
"back to SEN") does NOT change the retrieval where-filter — every turn stays
locked to the birth team. This is the 9f-deferred re-scope tail.

## Decision: MOVE
Anchor = the most-recently-named tracked team, seeded by the birth team.
Single team at any moment; never accumulates.

- ACCUMULATE was ruled out in 9f ("scope moves, it doesn't accumulate" — an
  accumulating set leaks the compared team into every later ranking and worsens
  the general-shelf mis-rank).
- MOVE chosen over ONE-OFF because the failure modes are asymmetric:
  - ONE-OFF's failure is INVISIBLE + unrecoverable — an unnamed follow-up
    ("why does that work?") silently reverts to the birth team and answers the
    wrong team with no flag. Same lie-shaped failure as 9f's flat rejection.
  - MOVE's failure is RECOVERABLE — a stray mention can swing the anchor, but
    the next named team corrects it.
- Consistent with the project's correctness-over-simplicity precedents
  (Option-B upload isolation, sessions-over-JWT, admit-biased SCOPE_THRESHOLD).

## Scope — session 1 (this build)
1. Deterministic entity detection: parse the user message for a tracked team
   name, map to `team_id` via `team_registry` (SSOT). Case-insensitive. NOT an
   LLM call — preserves the free-to-reproduce / testable property.
2. Effective-anchor resolution per turn:
   - exactly one distinct tracked team named → SWITCH (that team becomes anchor)
   - zero named → INHERIT the current anchor
   - two or more named → HOLD current anchor (comparison signal, see Deferred).
     Never silently swing to an arbitrary one of them.
3. Thread the resolved `team_id` (int) through `retrieve_merged` at the
   attempt-1 retrieval site — BOTH the shared-corpus filter AND the user's
   upload-collection filter.
4. Persist the RESOLVED team in the turn's `entity_scope` (chains the anchor to
   the next turn + keeps the per-turn scope auditable).

## Explicitly deferred — each gets its own numbered step, NOT session 1
- 9f attempt-2 "anchor rewrite" must inject the CURRENT anchor, not the birth
  team. This is the coupling and the real session-2 point: 9e changes what
  "the anchor" means inside code 9f already ships. Hard constraint carries over
  — the rewrite may inject only corpus-known terms (the team name), never
  invent specificity (measured 0.8276→0.5896 works; 0.5896→0.6315 when adding
  "on Ascent" the corpus can't back).
- Frontend: the thread-header badge must reflect the current anchor, not the
  birth team. Open display decision: birth / current / both. Session 2.
- Switch-vs-comparison detection ("does PRX play like MIBR?" names MIBR but is
  not a switch). Future refinement — the gate-becomes-a-router work.

## Hard constraints
- ChromaDB where-filters are EXACT-TYPE: `team_id` must go in as int `624`,
  never `"624"`. Wrong type = zero docs returned SILENTLY. (thrice-burned)
- A turn naming an UNTRACKED team → no re-scope (no docs exist to scope to);
  hold the current anchor.
- Detection is deterministic. No per-turn LLM parse (cost + breaks eval
  reproducibility).

## Open sub-decisions — lock at build STEP 1, before any code
1. State location for "current anchor":
   - (rec) DERIVE from the last user message's resolved `entity_scope.team_id`,
     seeded by `conversations.team_id` when history is empty. No schema change,
     no migration, reuses 9f's per-turn `entity_scope`, single source of truth.
   - vs STORE a new `conversations.current_team_id` column (explicit state,
     costs a migration + a dual-write to keep in sync).
2. `team_registry` shape: must review the file before writing the detector —
   does it hold aliases ("GE" vs "Global Esports"), a display name, the int id?
   Full-file review, no guessed signatures.

## Verification — session 1
- `run_eval` reproduces the frozen baseline BYTE-IDENTICAL (67%@1 / 44% served):
  when no turn re-scopes, attempt-1 with the birth anchor as the only named team
  must behave exactly as today. The refactor is behaviour-preserving on the
  no-re-scope path.
- New end-to-end (plain `uvicorn api:app`, no `--reload`): a PRX-born thread —
  T2 names GE → retrieval scopes to GE; T3 (no name) → inherits GE; T5 names PRX
  → back to PRX. Confirm per-turn `entity_scope.team_id` via Postgres read-back.