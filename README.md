# IGLA — In-Game Leader AI

Pre-match tactical intelligence for Valorant esports. Describe a scenario in
plain English and get a scouting report grounded in real opponent data.

Most teams below the top tier don't have a dedicated analyst desk. IGLA is a
stand-in for one: an analyst types a situation, the system pulls the relevant
opponent intel out of a vector database, and Claude writes it up as a structured
dossier. It's for prep, not live coaching — you use it before the match, not
during the round.

Repo: https://github.com/412-Raghav/IGLA-ai

## Demo

Describe a pre-match scenario in plain language:

![User input to IGLA](assets/ui-input.png)

IGLA returns a structured tactical read. The opponent's behavioral profile is
pulled from the knowledge base; the step-by-step plan is the model reasoning over
that profile.

![IGLA tactical brief](assets/ui-response.png)

Running in production — scheduled data refresh, a persistent vector store, and the
full request path visible in the logs: the scope-gate distance, the model call,
and the response.

![Deployment logs](assets/production-logs.png)

## How it works

You send a natural-language situation. The retriever finds the most relevant
documents for the team you're scouting, that context gets attached to the prompt,
and Claude answers using it. The interesting parts are what's in the database and
how retrieval is kept honest.

The store holds three kinds of document, all tagged by where they came from:

- **Live player stats** scraped per team from a community stats site — agent
  usage, roles, firepower, refreshed on a schedule.
- **Generated team briefs** — one per team, written by Claude at build time from
  a fact sheet assembled out of those stats.
- **Hand-curated intel** — the deeper, specific tactical notes that a human
  analyst would write.

Retrieval is scoped by team. A query about one opponent only searches that
opponent's documents plus a shelf of universal theory (agent combos, timing
principles), so a twelve-team corpus doesn't turn into twelve teams of noise.

## Engineering notes

The parts worth calling out, since a working RAG demo is easy and a trustworthy
one isn't.

### Grounding the generator by construction

Asking a model to "only use these facts" and hoping is not a plan. A generator
staring at a thin prompt will fill the gaps from its own training data and hand
it to you as if it were real intel.

So the generated team briefs are grounded structurally, not by request. The model
never sees the raw stats or anything else — it sees a single fact sheet built in
plain Python, containing only what the stats actually support: role composition,
agent pools, who carries by rating and combat score, and which players have too
little data to judge. If a fact isn't on the sheet, the model has nothing to draw
on to state it. A system prompt on top forbids it from asserting anything the
stats can't contain (map calls, economy reads, round tendencies) and from
dressing up its general Valorant knowledge as team-specific analysis.

Generation runs at temperature 0. For a grounded writer, variety is the failure
mode — every bit of sampling randomness is a chance to wander off the sheet.

### A human gate that actually caught things

Nothing generated gets embedded without a person reading it first, against the
exact fact sheet it was written from (the sheet is frozen and stored next to the
output for that reason — stats drift, so the doc is judged against its own source,
not a fresh pull).

This isn't ceremony. The gate caught a brief that invented a roster structure the
data contradicted, and another that called two different players the team's
highest-rated in the same breath. Both were real errors that would have gone into
the database silently otherwise. Both got fixed at the prompt level and
regenerated.

### Same model, two jobs, opposite risk

At query time Claude reasons over documents that retrieval already pinned — it
can't invent opponent intel, because the facts are in front of it. At build time,
generating briefs from sparse input, it can invent freely. Those are two
different risk profiles from one model, and the build-time path is why the fact
sheet, the contract, and the human gate all exist. The query path doesn't need
them; the generation path can't do without them.

### The generator never touches the serving path

Generation is a manual step an operator runs when a roster changes. The output is
reviewed, committed, and loaded as cached text. The daily refresh re-scrapes stats
and re-embeds them locally — it spends zero LLM tokens. The serving code doesn't
import the generator at all. The cost model was a design decision, not an
afterthought: putting generation inside the refresh would have quietly turned a
free daily job into a paid one.

### Retrieval is measured, not assumed

There's an evaluation harness that scores retrieval on its own — hit-rate@k
against a frozen set of analyst queries mapped to the documents that should come
back. It runs without calling the model, so a bad answer can be blamed on
retrieval or on writing, separately.

It's earned its keep. It caught a mislabeled entry in its own answer key before
that corrupted the numbers. It caught a regression where a fix that helped one
query broke another. And it's where the retrieval limitation below was found,
rather than in production.

The concrete win it drove: top-1 retrieval went from 44% to 78% by injecting
derived role metadata into the documents — a game-wide agent-to-role mapping that
lets a jargon query like "best duelist" find a raw stat line it doesn't share any
words with. That's a measured improvement from a cheap technique, which reads
better than swapping in a bigger model and hoping.

### Guardrails

Three layers, each doing one job:

- A scope-gate rejects anything that isn't a tactical question before the model is
  ever called, based on how far the closest document sits from the query. It
  doubles as a free spam filter — nonsense gets turned away for nothing.
- User input and retrieved context are wrapped in delimiters that tell the model
  to treat them as data, not instructions. This is hygiene, not a wall — prompt
  injection isn't a solved problem industry-wide, and this doesn't pretend to
  solve it.
- IP-based rate limiting on the query endpoint. In-memory and single-instance,
  which is honest about its scale; a shared store is the path if it ever runs on
  more than one.

## Tech stack

| Layer         | Choice                                        |
|---------------|-----------------------------------------------|
| Language      | Python 3.13                                   |
| LLM           | Anthropic Claude (Sonnet)                     |
| Vector DB     | ChromaDB (local, persistent)                  |
| Embeddings    | all-MiniLM-L6-v2 (local)                       |
| API           | FastAPI + Uvicorn                             |
| Data source   | Unofficial community Valorant stats client    |
| Deployment    | Railway (web service + separate cron service) |

Dependencies are pinned to exact versions so the build is reproducible.

## Running it locally

```bash
python -m venv venv
venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

Set your Anthropic API key in a `.env` file, then populate the database:

```bash
python ingest.py
```

Query it directly:

```bash
python main.py
```

Or run the API:

​```bash
uvicorn api:app --reload
​```

## API

​```
POST /ask
{ "situation": "how should we defend against a fast B execute" }
​```

`GET /health` is an unauthenticated liveness check. Refreshing the live data is
behind a secret-protected endpoint, triggered on a schedule by a separate cron
service rather than an in-process timer.


## Limitations, and what was done about them

Every real system has edges. These are IGLA's, and what each one got instead of
being ignored.

**Data freshness.** Rosters and stats come from a community site, and that feed
can lag reality — a benched or newly-signed player might linger in a team's
profile, or not appear yet, for a while after a change. The stats window is also
wide enough that experimental play at exhibition events can colour a team's
tendencies. Neither was hidden. The time window is a parameter you can tighten
during active season, and the pre-match framing means the analyst reading the
report already knows who's actually playing. The proper fix — cross-checking a
second source and reconciling the disagreements — is genuinely its own piece of
work (matching one player across two sites is harder than it looks), so it's
scoped as a future phase instead of bolted on badly.

**Retrieval on jargon-heavy queries.** The retriever sometimes ranks a general
tactical document above the specific answer, because the embedding model treats
dense tactical vocabulary as broadly relevant — a "best duelist" query can pull a
general defensive-combo note ahead of the actual duelist's stats. This one's
worth being straight about, because of how it was handled. The evaluation harness
found it. The cause was diagnosed: universal theory documents sit in every team's
candidate pool and out-rank team-specific ones on shared keywords. The test set
was left untouched — editing it to recover the number would have been measuring
nothing. Two fixes are on the table: a re-ranking pass that prefers team-specific
documents when a team is in scope, or a stronger embedding model validated against
the same harness. It was documented and measured rather than patched in a hurry
that could regress other queries.

**Scope.** Pre-match only. It doesn't do live in-round coaching and isn't meant
to.

**No SLA on the data source.** The stats client is an unofficial community
project. Coverage is good, but if the underlying site changes, ingestion needs
updating.

## What's next

- Letting users upload their own scouting notes and scrims into a team's context.
- A focus control so an analyst can point the system at one opponent and steer the
  prompt from the UI.
- User accounts, so history and per-user on-demand refresh become possible.
- Cross-source data integrity — reconciling the stats feed against a second source
  to catch the roster-lag problem above.