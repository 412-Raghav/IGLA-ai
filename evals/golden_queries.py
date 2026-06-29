"""Golden evaluation set for IGLA retrieval.

Each entry maps an analyst-style query to the doc ID that SHOULD be the
top retrieval result. This is GROUND TRUTH — it encodes what "correct
retrieval" means for IGLA. The analyst sets it, not the model.

Doc-ID convention: vlr-player-{vlr_player_id}.
Verify every expected_doc_id against real collection IDs (peek output)
before trusting any score this set produces.
"""

GOLDEN_QUERIES = [
    # --- Sanity anchors: a player's name must return that player's doc.
    # Ground truth is unambiguous. If these miss, retrieval is broken.
    {"query": "invy", "expected_doc_id": "vlr-player-8504"},
    {"query": "Jinggg", "expected_doc_id": "vlr-player-7378"},
    {"query": "f0rsakeN", "expected_doc_id": "vlr-player-9801"},
    {"query": "d4v41", "expected_doc_id": "vlr-player-9803"},
    {"query": "something", "expected_doc_id": "vlr-player-17086"},

    # --- Jargon probes: analyst terms, not names. THIS is the gap.
    # YOU set expected_doc_id from the current roster + your docs.
    # A wrong guess here poisons the metric, so None stays None.
    # --- Jargon probes: analyst terms, not names. Each label set from
    # scraped agent tendencies (peek @500), not memory. See role table.
    {"query": "best duelist", "expected_doc_id": "vlr-player-17086"},      # something: Jett 65%, 0.19 FK/rd
    {"query": "entry fragger", "expected_doc_id": "vlr-player-17086"},     # something: roster-max first kills
    {"query": "primary initiator", "expected_doc_id": "vlr-player-8504"},  # invy: Sova/Skye/Breach
    {"query": "main controller", "expected_doc_id": "vlr-player-9801"},    # f0rsakeN: Omen 58%
]