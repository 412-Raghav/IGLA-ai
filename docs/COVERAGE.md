# IGLA — Coverage & Operations

**Audience:** operators and maintainers running or extending IGLA.

**Not this doc:** the project narrative, architecture rationale, and design tradeoffs live in
the [README](../README.md). This is the runbook — what's covered, how to add a team, how the
system scales, and what to watch when the data looks wrong.

**Source of truth:** `data/team_registry.py`. If this document and the registry ever disagree,
the registry wins. The table below is a human-readable summary; the registry is authoritative
for team tags and IDs.

---

## Current coverage

12 VCT teams across 4 regions (3 per region). Primary development and evaluation team:
**Paper Rex** (`team_id` 624).

The list below mirrors `data/team_registry.py`; the registry stays authoritative. `team_id` is
stored as **int** — the same int the retrieval filter keys on (a string ID fails the where-filter
silently).

| Region | Team | `team_id` |
|---|---|---|
| Americas | Sentinels | 2 |
| Americas | G2 Esports | 11058 |
| Americas | LEVIATÁN | 2359 |
| EMEA | Fnatic | 2593 |
| EMEA | Team Vitality | 2059 |
| EMEA | Team Liquid | 474 |
| Pacific | Paper Rex | 624 |
| Pacific | Global Esports | 918 |
| Pacific | DRX &sup1; | 8185 |
| China | EDward Gaming | 1120 |
| China | Xi Lai Gaming | 13581 |
| China | FunPlus Phoenix | 11328 |

&sup1; vlr.gg lists this org as **KIWOOM DRX** under its title sponsor — search that when resolving
its roster. Team names above match vlr.gg's exact styling (capitalization and diacritics) so an
exact-match roster lookup doesn't miss.

**What each team contributes to the corpus:**

- **vlr.gg player docs** (`source:"vlr.gg"`) — live roster and per-player stats, roster-dependent
  in count (typically ~5; sparse or off-season rosters show fewer and are kept honestly — low data
  is still intel). Refreshed daily.
- **1 generated strategy doc** (`source:"generated"`) — a grounded team baseline, operator-generated
  (see [Adding a team](#adding-a-team)).
- **Hand-authored deep docs** (`source:"curated"`) — Paper Rex carries 4, Fnatic carries 2. These
  are manually written intel (playstyle, map tendencies, executes), distinct from the generated
  baselines. Other teams currently have none.

Plus a region-agnostic theory shelf (`scope:"general"`) shared across every team's candidate pool.

**Corpus today:** 77 docs.

| Tier | Count | Tag |
|---|---|---|
| Curated | 8 | `source:"curated"` — 4 PRX + 2 FNC (team-tagged) + 2 general (`scope:"general"`) |
| Generated team baselines | 12 | `source:"generated"` |
| vlr.gg live player docs | 57 | `source:"vlr.gg"` |

Every doc is provenance-tagged. `evals/check_filter.py` verifies partitioning: for each team, the
count of foreign-team docs in its scoped pool is 0.

---

## Adding a team

Onboarding a team is an operator-run sequence. Steps 4–5 cost Anthropic tokens (one-time,
generation only); everything else is local scrape + embed and costs no tokens.

1. **Find and roster-verify the vlr.gg `team_id`.** Do not trust the first search hit — confirm
   the returned roster matches the team you intend. Store the ID as an **int** (ChromaDB
   where-filters are exact-type; `624` and `"624"` are different keys and the mismatch fails
   silently).

2. **Add the team to `data/team_registry.py`** — the SSOT (region, tag, `team_id`).

3. **Run the live ingest** to pull roster and player stats from vlr.gg. This creates the
   `source:"vlr.gg"` player docs. The batch loop is fail-soft: a team that errors is logged and
   skipped, not fatal.

4. **Generate the team's strategy doc** — run `data/generate_docs.py` for the new team. This
   builds a grounded fact brief from raw vlr stats, then generates a doc at `temperature=0`, and
   persists `data/generated/<TAG>_<id>.json` with the **frozen brief stored alongside the generated
   text**. (`generate_docs.py` is imported by nothing in the serving path — generation stays off
   the daily cron, so the cron costs zero tokens.)

5. **Human review gate — not optional.** Read the generated doc against its frozen brief. The
   generator is a court reporter, not a novelist: it may assert role composition, agent pools,
   flex, firepower, and sparse-data notes **only**. Reject and reroll if you see:
   - inferred structure ("standard five-role" on a roster that isn't five roles),
   - superlative contradictions (two players both called "highest rating"),
   - any map / execute / economy / round-tendency claim (out of scope by contract),
   - general Valorant knowledge dressed up as team-specific intel.

   **Re-read every reroll.** A regenerated doc can reintroduce a different error; a prior fix once
   passed review on a first read and failed on the re-read. Trust the re-read, not the reroll.

6. **Ingest the reviewed doc** — `ingest_generated_docs()` loads `data/generated/*.json` tagged
   `source:"generated"`, `team_id` int.

7. **Verify isolation** — run `evals/check_filter.py` (it uses `collection.get()`, an exact
   metadata scan; `query()`'s ANN candidate ceiling makes it the wrong tool for set-membership).
   Confirm the new team's shelf equals its player docs + 1 generated doc, and its foreign-team
   count is 0.

8. **Leave the golden set frozen.** `evals/golden_queries.py` is the held-out retrieval baseline.
   Onboarding a team does not require touching it. If you later want to evaluate retrieval quality
   for the new team, that is a separate, deliberate addition — never an edit made to recover a
   number.

---

## Scaling

- **Corpus growth is linear and predictable.** Each team adds ~5–6 docs (roster-dependent player
  docs + 1 generated). 12 teams → 77 docs today.
- **Cross-team isolation holds as you scale**, because it's enforced by the `team_id` filter, not
  by hoping the embedding space separates teams. Re-run `check_filter.py` after each add to confirm
  foreign-team count stays 0 — treat it as the onboarding regression test.
- **Cost scales with usage, not coverage.** The daily refresh scrapes vlr.gg and re-embeds with a
  local model — zero Anthropic tokens. Adding teams grows scrape time and DB size, not daily token
  cost. Tokens are spent only on (a) user `/ask` queries and (b) operator generation runs.
- **Rate limiting is single-replica.** slowapi's in-memory store is correct for one web replica. If
  you scale to multiple replicas, move the limiter to a shared store (Redis) — the counters won't
  otherwise be shared across replicas.
- **Cold-boot embedder download.** The default embedding model (~79MB ONNX) re-downloads on cold
  boots only, adding ~8s to cold-start latency — no effect on warm restarts or request latency.
  Deferred; pairs with a future embedding-model upgrade.

---

## Refresh cadence

- **Live data (player stats):** daily cron at `0 6 * * *` UTC, driven by a separate Railway
  pinger service that hits the secret-protected `/refresh`. Zero tokens. Keeps rosters and
  per-player stats current.
- **Generated docs:** operator-run, periodic — not on the cron (cost separation). Regenerate a
  team's doc when its roster changes materially or at the start of a new competitive split. Always
  re-run the human review gate on the regenerated doc.

---

## Data-quality watchlist

These are the failure modes an operator will actually notice. Each is a *check-and-act*, not just
a caveat.

- **Roster endpoint lag.** vlr.gg's roster endpoint can list a benched/inactive player as active.
  A player moved to inactive on 2026-04-28 was still returned as active and got pulled into that
  team's brief. **Action:** sanity-check briefs against known roster moves; where it matters, note
  the discrepancy. This is the motivating case for a future cross-source integrity phase (a second
  source such as Liquipedia's MediaWiki API — entity matching + conflict resolution, its own phase,
  not a quick patch).
- **Exhibition-event pollution.** A rolling stats window can absorb non-competitive matches, so a
  team's numbers can drift right after a large exhibition event. **Action:** be skeptical of sudden
  stat swings following exhibitions; flag in review rather than treating them as form.
- **Partial off-season rosters.** Some teams surface only 3 players off-season. **Action:** sparse
  rosters are retained deliberately (low data is intel), but flag when a team looks thin so a reader
  doesn't mistake incompleteness for a scouting conclusion.

---

## Retrieval knobs (operator-facing)

- **Scope gate.** A semantic scope-gate rejects off-topic queries whose best match falls beyond a
  distance threshold (set via the `SCOPE_THRESHOLD` env var). It was calibrated on real probes.
  **Recalibrate it if you change the embedding model** — the distance scale shifts with the model.
- **Prompt-injection hygiene.** Retrieved context and the user situation are wrapped in
  `<untrusted_data>` tags before reaching the model. This is hygiene (relevance filtering), not a
  security boundary — treat it as such.
