# Production Samples

Raw, unedited artifacts captured from the live Railway deployment before the
free-tier credit lapsed. Preserved as a provenance record, not as a demo.

## What was running

- Deployed commit: `a665b70` "feat: team-first re-rank on scoped retrieval"
  Verified two ways: `git log --oneline -1 origin/main`, and the commit subject
  on Railway's ACTIVE deployment. (`b14c90f2` in the Railway UI is a deployment
  id, not a git SHA.)
- Python 3.13.14, US West, 1 replica, ChromaDB on a persistent volume.
- Embeddings: all-MiniLM-L6-v2, 384-dim. SCOPE_THRESHOLD = 0.75.
- Corpus at boot: 8 static tactical docs + 57 vlr.gg player docs across 12
  teams = 65 documents. (See "Known gaps" below.)

## The API contract at this commit

`POST /ask` accepted a single field:

    { "situation": "<free text>" }

There was no `team_id`. There was no `GET /teams`. Retrieval ran **unscoped**
across the whole collection.

## What `ask_a665b70_2026-07-09.json` shows

A `200` response to a query naming Paper Rex. Server-dated
`Thu, 09 Jul 2026 23:45:52 GMT`; Railway request id `U93wSXA0TL-V1T1s2h0iww`.

Measured from application logs for this exact request:

| Phase      | Elapsed |
|------------|---------|
| Retrieval  | 2.75 s  |
| Generation | 13.0 s  |
| Total      | 15.7 s  |

`best_distance = 0.5922`, comfortably inside the 0.75 gate.

## What it does NOT show, and why that is the point

The response is fluent, well-formatted, and names the opponent. It contains
**zero player names**, despite 57 player documents in the collection.

Because `/ask` took no `team_id`, the scoped-retrieval and team-first re-rank
engineering behind commit `a665b70` was never reachable from the serving path.
The opponent-specific content in this dossier is more plausibly the language
model's pretraining knowledge than anything retrieved.

Production cannot settle the question. The retrieval log line records
`best_distance` and nothing else -- no document ids, no sources. Which documents
were retrieved is unknowable from the evidence. That is a defect, tracked as
observability gap #1.

At least one claim in the dossier is mechanically false: Killjoy's Nanoswarm
denies defusal; it does not delay spike detonation.

This sample is the "before" panel of a diff. The fix is commit `027291e`,
which makes `team_id` required.

## Known gaps visible in the boot log

- Production ingested 65 documents. The local corpus is 77. The 12 generated
  team documents appear in no ingest line. PENDING local verification with
  `evals/peek.py`.
- Team Liquid and FPX returned 3 player docs each; every other team returned 5
  or 6. Retrieval against those teams searches a smaller pool.

## Files

- `ask_a665b70_2026-07-09.json` -- raw `/ask` response body, unedited.

## Handling note

The HTTP-log export from Railway contains a residential source IP and must be
redacted before it enters this repository. The deploy-log export contains
infrastructure UUIDs, which should be stripped. Neither is committed here.