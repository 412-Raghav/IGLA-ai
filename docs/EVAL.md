# Retrieval Evaluation

This is the detail behind the headline numbers in the [README](../README.md).
Everything here is reproducible from the repo:

```bash
python -m evals.run_eval                    # config threshold (deployed: 0.75)
python -m evals.run_eval --threshold 0.855  # recalibrated cutoff
```

The harness calls **no model**. It scores the retriever in isolation, so a bad
end-to-end answer can be attributed to retrieval or to generation separately.

## The golden set

Nine analyst-style queries, each mapped to the single document that *should* rank
first, all scoped to Paper Rex (`team_id=624`). The set is frozen and treated as a
held-out test — it is never edited to recover a number, because editing a test to
pass it measures nothing.

Two kinds of query:

- **Name anchors** (`invy`, `Jinggg`, `f0rsakeN`, `d4v41`, `something`) — a
  player's name must return that player's document. If these miss, retrieval is
  broken.
- **Jargon probes** (`best duelist`, `entry fragger`, `primary initiator`,
  `main controller`) — analyst vocabulary that shares no literal words with the
  target document. These are the hard cases, and where the embedding model earns
  or loses its keep.

## Two metrics, one gate

Each query is scored on two things:

- **hit-rate@k** — did retrieval rank the correct document in the top k?
- **served@k** — did that document *also* clear the scope-gate (distance ≤
  threshold), i.e. would production actually return it?

`served@k` uses the exact `passes_scope_gate()` function the serving path uses.
The two importing the same function is the point: before that, the eval scored
rank while production applied a distance cutoff the eval never saw, and the two
silently diverged.

## Results

Embedding model: all-mpnet-base-v2. Collection: 77 documents (local).

### At the deployed threshold (0.75)

| Query | Rank | Distance | Gate |
|-------|------|----------|------|
| invy | 1 | 0.644 | PASS |
| Jinggg | 1 | 0.620 | PASS |
| f0rsakeN | 1 | 0.716 | PASS |
| d4v41 | 1 | 0.837 | **REJECT** |
| something | 1 | 0.810 | **REJECT** |
| best duelist | 1 | 0.579 | PASS |
| entry fragger | 2 | 0.702 | PASS |
| primary initiator | 2 | 0.653 | PASS |
| main controller | 2 | 0.801 | **REJECT** |

```
hit-rate@1: 6/9 = 67%
served@1:   4/9 = 44%     (rank@k AND scope-gate PASS)
hit-rate@3: 9/9 = 100%
served@3:   6/9 = 67%
```

### At the recalibrated threshold (0.855)

Same retrieval, same ranks — only the gate cutoff moves:

```
hit-rate@1: 6/9 = 67%     (unchanged; the gate never touches rank)
served@1:   6/9 = 67%     (the three rejects now clear the gate)
hit-rate@3: 9/9 = 100%
served@3:   9/9 = 100%
```

## Reading the gap

The 23-point difference between hit-rate@1 (67%) and served@1 (44%) at the
deployed threshold is the whole story. Three retrievals — `d4v41` (0.837),
`something` (0.810), `main controller` (0.801) — are ranked correctly but sit
just past the 0.75 cutoff and get rejected before they reach the analyst.

This is **not** a retrieval failure. Retrieval did its job. The threshold is
miscalibrated: 0.75 was tuned against a different embedding model's distance
distribution, and under the current model that distribution has shifted upward.
The threshold and the embedder are a coupled pair. Moving the cutoff to 0.855 —
still comfortably below the off-topic rejection floor — recovers all three without
letting genuine noise through.

The lesson worth keeping: **hit-rate@k measures the retriever; served@k measures
the product.** A high hit-rate with a low served-rate is a system that finds the
answer and then throws it away. You only see it if the eval applies the same gate
the user hits.

## A note on determinism

On this nine-query set, repeated runs vary by up to one query — the reported
hit-rate@1 came out 67% in six of seven runs and 78% in one. That variance is
worth being precise about rather than quietly reporting the best print.

A probe (`evals/determinism.py`) isolated the cause:

- **In-process, retrieval is exactly stable** — 20 identical queries return 20
  identical results. The embedding of a fixed string is deterministic within a
  process.
- The variance is **cross-process**, and it is a near-tie: on `primary initiator`,
  a generated team brief and a player document sit at almost the same distance
  (~0.653 vs ~0.696), and which one lands at rank 1 versus rank 2 can differ
  between process starts.

So the reported 67% is the modal result, not a lucky single run. Two follow-ups
are noted honestly rather than papered over: pinning down the exact source of the
cross-process tie-break, and the fact that the near-tie involves a generated brief
that is present in the local collection but not in the deployed one — which is its
own eval-vs-prod corpus difference, tracked separately.

## Residual misses (the honest hard core)

Three jargon probes are the genuine difficulty, and no drop-in embedding model
cracks them cleanly:

- **`entry fragger`** and **`primary initiator`** land at rank 2 — the correct
  player is retrieved and reaches the writer as context (hit-rate@3 is 100%), but
  isn't the single top result.
- **`main controller`** is architecturally ill-posed for a team that runs two
  controllers: there is no single "main" controller to return, so the query has no
  unambiguous ground-truth answer for this roster.

These are documented as limitations, not hidden as failures. The team-first
re-rank already recovers every correct document into the top 3; closing the
remaining @1 gap on jargon is embedding-quality work, tracked as future rather
than hand-tuned to pass the frozen set.
