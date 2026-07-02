"""Golden evaluation set for IGLA retrieval.

Each entry maps an analyst-style query to the doc ID that SHOULD be the
top retrieval result, plus the team_id whose shelf the query is scoped
to. This is GROUND TRUTH -- it encodes what "correct retrieval" means
for IGLA. The analyst sets it, not the model.

team_id scopes each query the way the LIVE APP retrieves: an analyst
prepping against one team searches THAT team's shelf, not the whole
scene. All current queries are PRX (624). A query with team_id=None
(or the field omitted) searches the full corpus.

Doc-ID convention: vlr-player-{vlr_player_id}.
Verify every expected_doc_id against real collection IDs (peek output)
before trusting any score this set produces.
"""

GOLDEN_QUERIES = [
    # --- Sanity anchors: a player's name must return that player's doc.
    # Ground truth is unambiguous. If these miss, retrieval is broken.
    {"query": "invy", "expected_doc_id": "vlr-player-8504", "team_id": 624},
    {"query": "Jinggg", "expected_doc_id": "vlr-player-7378", "team_id": 624},
    {"query": "f0rsakeN", "expected_doc_id": "vlr-player-9801", "team_id": 624},
    {"query": "d4v41", "expected_doc_id": "vlr-player-9803", "team_id": 624},
    {"query": "something", "expected_doc_id": "vlr-player-17086", "team_id": 624},

    # --- Jargon probes: analyst terms, not names. Each label set from
    # scraped agent tendencies (peek), not memory. See role table.
    {"query": "best duelist", "expected_doc_id": "vlr-player-17086", "team_id": 624},      # something: Jett 65%, 0.19 FK/rd
    {"query": "entry fragger", "expected_doc_id": "vlr-player-17086", "team_id": 624},     # something: roster-max first kills
    {"query": "primary initiator", "expected_doc_id": "vlr-player-8504", "team_id": 624},  # invy: Sova/Skye/Breach
    {"query": "main controller", "expected_doc_id": "vlr-player-9801", "team_id": 624},    # f0rsakeN: Omen 58%
]