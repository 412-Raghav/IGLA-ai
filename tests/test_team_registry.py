"""Unit tests for team_registry.teams_mentioned — pure, no DB, no network."""

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