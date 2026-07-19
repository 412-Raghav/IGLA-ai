"""STEP 1: measure injected-query distances against the real retrieval path.

Zero Anthropic tokens -- the scope-gate fires on embedding distance, upstream
of the model. Calls retrieve_ranked (the exact path /ask uses) so the numbers
are what production would see, not a reimplementation that scores a fiction.

Question this settles: does a pure-discourse follow-up ("why does that work?")
clear the gate once the anchor team is injected? If yes, bucket (1) is one
rewrite path. If no, the templated clarify question (1b) is a real code path we
have to design.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SCOPE_THRESHOLD  # noqa: E402  (after path bootstrap)
from rag.retriever import passes_scope_gate, retrieve_ranked  # noqa: E402

PRX = 624  # Paper Rex -- the thread anchor injected into every rewrite below.

# (label, query, baseline) -- baseline is the raw query in the same group, so
# the printed change isolates what the injection buys. Casing is held constant
# within a group so the ONLY variable is the injected anchor name.
QUERIES = [
    ("A0", "why does that work?", None),                    # pure discourse, raw
    ("A1", "why does that work for Paper Rex?", "A0"),      # natural suffix
    ("A2", "Paper Rex why does that work?", "A0"),          # crude prepend
    ("B0", "how do they attack B site?", None),             # referential, raw
    ("B1", "how does Paper Rex attack B site?", "B0"),      # clean substitution
    ("B2", "Paper Rex how do they attack B site?", "B0"),   # prepend, pronoun kept
]


def main() -> None:
    print(f"SCOPE_THRESHOLD = {SCOPE_THRESHOLD}   scope = Paper Rex (team_id={PRX})\n")
    header = f"{'':4}{'query':40}{'best_dist':>11}{'gate':>9}{'chg vs raw':>12}"
    print(header)
    print("-" * len(header))

    distances: dict[str, float | None] = {}
    for label, query, baseline in QUERIES:
        _, _, best = retrieve_ranked(query, team_id=PRX)
        distances[label] = best
        gate = "PASS" if passes_scope_gate(best) else "REJECT"

        if best is None:
            print(f"{label:4}{query:40}{'None':>11}{gate:>9}{'-':>12}")
            continue

        base = distances.get(baseline) if baseline else None
        chg = f"{best - base:+.4f}" if base is not None else "-"
        print(f"{label:4}{query:40}{best:>11.4f}{gate:>9}{chg:>12}")


if __name__ == "__main__":
    main()
