"""Unit tests for team_registry.teams_mentioned — pure, no DB, no network."""

import pytest

from data.team_registry import teams_mentioned


def test_single_canonical_name():
    assert teams_mentioned("How does Paper Rex attack Lotus?") == {624}


def test_case_insensitive():
    assert teams_mentioned("how does paper rex hold?") == {624}
    assert teams_mentioned("GLOBAL ESPORTS looked strong") == {918}


def test_no_team_named_returns_empty():
    assert teams_mentioned("why does that work?") == set()


def test_two_distinct_teams():
    assert teams_mentioned("compare Paper Rex with Global Esports") == {624, 918}


def test_same_team_twice_dedups():
    assert teams_mentioned("Paper Rex early, Paper Rex late") == {624}


def test_word_boundary_rejects_partial():
    # "Sentinels" is the tracked name; the singular "sentinel" must not match.
    assert teams_mentioned("the sentinel watched the site") == set()
    assert teams_mentioned("Sentinels won the map") == {2}


def test_bare_team_word_does_not_match():
    # "Team Liquid"/"Team Vitality" exist; a lone "team" names no one.
    assert teams_mentioned("good team play out there") == set()


def test_accented_name_case_folds():
    assert teams_mentioned("Leviatán ran a fast push") == {2359}
    assert teams_mentioned("leviatán") == {2359}


def test_empty_string():
    assert teams_mentioned("") == set()


# --- Alias detection (9e-aliases) ------------------------------------------
# Every tracked team's shorthand resolves to the same team_id as its
# canonical name. Red-before-green: these fail on the canonical-only
# registry (set() != {id}) until STEP 2 folds aliases into _NAME_TO_ID.

@pytest.mark.parametrize(
    "text, expected_id",
    [
        ("SEN took pistol on Ascent", 2),
        ("G2 default setup mid", 11058),
        ("LEV ran a fast A", 2359),
        ("FNC mid control on Haven", 2593),
        ("VIT retake protocol", 2059),
        ("TL slow default B", 474),
        ("how does PRX attack Lotus?", 624),
        ("why does this not work for GE?", 918),
        ("EDG on the China patch", 1120),
        ("XLG comp on Bind", 13581),
        ("FPX aggressive entries", 11328),
        ("Liquid holds B site", 474),
        ("Vitality runs double controller", 2059),
        ("Leviatan ran a fast push", 2359),
    ],
    ids=[
        "SEN", "G2", "LEV", "FNC", "VIT", "TL", "PRX", "GE",
        "EDG", "XLG", "FPX", "Liquid", "Vitality", "Leviatan-ascii",
    ],
)
def test_alias_resolves(text, expected_id):
    assert teams_mentioned(text) == {expected_id}


def test_alias_case_insensitive():
    # Aliases fold case the same way canonical names do, and dedup.
    assert teams_mentioned("prx early, prx late") == {624}
    assert teams_mentioned("gE looked strong today") == {918}


# --- False-positive guards (regression, must stay green) -------------------
# Short aliases are substring-risky; \b must stop them firing inside
# ordinary words. These pass on the canonical-only registry too (nothing
# matches yet) — their job is to fail loudly if STEP 2 drops the \b anchor
# or adds a careless alias. GE and EDG (accepted-risk tags) get extra rows.

@pytest.mark.parametrize(
    "text",
    [
        "the edge of the site",       # EDG vs edge
        "hedge your utility early",   # EDG vs hedge
        "why does that change?",      # GE vs change
        "massive damage output",      # GE vs damage
        "vital comms on defense",     # VIT vs vital
        "take it to the next level",  # LEV vs level
        "that makes no sense",        # SEN vs sense
        "the leviathan of the deep",  # LEV/Leviatan vs leviathan
    ],
    ids=[
        "EDG-in-edge", "EDG-in-hedge", "GE-in-change", "GE-in-damage",
        "VIT-in-vital", "LEV-in-level", "SEN-in-sense", "LEV-in-leviathan",
    ],
)
def test_short_alias_no_substring_false_positive(text):
    assert teams_mentioned(text) == set()


def test_duplicate_alias_across_teams_raises():
    # A label mapping to two different teams is a registry bug, caught at
    # build time rather than silently mis-routing one team (What-if-B guard).
    from data.team_registry import _build_name_to_id

    colliding = [
        {"team_id": 1, "name": "Alpha", "aliases": ["X"]},
        {"team_id": 2, "name": "Beta", "aliases": ["X"]},
    ]
    with pytest.raises(ValueError):
        _build_name_to_id(colliding)