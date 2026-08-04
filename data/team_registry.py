"""Registry of Valorant teams IGLA tracks — the single source of truth.

Every team_id here is verified from vlr.gg via search_teams (never
guessed) and confirmed to be the active MAIN roster, not an Academy,
GC, Youth, or inactive squad. ingest.py loops over this registry to
scrape live tendencies. The README coverage list is derived from it.

To add a team: resolve its real team_id with vlr.search.search_teams,
confirm the roster with vlr.teams.roster, then append a row. Shorthands
analysts type ("PRX", "GE") go in the optional `aliases` list and resolve
to the same team_id as the canonical name. To scale to the full VCT
scene, see the coverage-expansion guide.
"""

import re

TRACKED_TEAMS = [
    # Americas
    {"team_id": 2, "name": "Sentinels", "region": "Americas", "aliases": ["SEN"]},
    {"team_id": 11058, "name": "G2 Esports", "region": "Americas", "aliases": ["G2"]},
    {"team_id": 2359, "name": "LEVIATÁN", "region": "Americas", "aliases": ["LEV", "Leviatan"]},
    # EMEA
    {"team_id": 2593, "name": "Fnatic", "region": "EMEA", "aliases": ["FNC"]},
    {"team_id": 2059, "name": "Team Vitality", "region": "EMEA", "aliases": ["VIT", "Vitality"]},
    {"team_id": 474, "name": "Team Liquid", "region": "EMEA", "aliases": ["TL", "Liquid"]},
    # Pacific
    {"team_id": 624, "name": "Paper Rex", "region": "Pacific", "aliases": ["PRX"]},
    {"team_id": 918, "name": "Global Esports", "region": "Pacific", "aliases": ["GE"]},
    {"team_id": 8185, "name": "DRX", "region": "Pacific", "aliases": []},  # vlr lists as "KIWOOM DRX" (title sponsor)
    # China
    {"team_id": 1120, "name": "EDward Gaming", "region": "China", "aliases": ["EDG"]},
    {"team_id": 13581, "name": "Xi Lai Gaming", "region": "China", "aliases": ["XLG"]},
    {"team_id": 11328, "name": "FunPlus Phoenix", "region": "China", "aliases": ["FPX"]},
]


TRACKED_TEAM_IDS = frozenset(team["team_id"] for team in TRACKED_TEAMS)


def team_ids():
    """Return every tracked team_id, for the ingest loop."""
    return [team["team_id"] for team in TRACKED_TEAMS]


def is_tracked(team_id: int) -> bool:
    """True when team_id names a team IGLA holds intel for.

    O(1) membership against a frozenset built once at import, so the
    request path never rebuilds the list. team_ids() stays as-is: the
    ingest loop wants an ordered list, the API wants a set.
    """
    return team_id in TRACKED_TEAM_IDS


_TEAM_NAMES = {team["team_id"]: team["name"] for team in TRACKED_TEAMS}


def team_name(team_id: int) -> str:
    """Display name for a tracked team_id, O(1) from a dict built at import.

    Used to inject the thread anchor into a rewritten query. Raises KeyError
    on an unknown id -- the caller holds a team_id that already passed
    is_tracked at conversation birth, so a miss here is a broken invariant
    (surface it as a 500), not user input to absorb.
    """
    return _TEAM_NAMES[team_id]


def _build_name_to_id(teams: list[dict]) -> dict[str, int]:
    """Map every lowercased label (canonical name + aliases) to its team_id.

    Runs once at import to build the lookup the matcher reads. A label that
    resolves to two *different* teams is a registry bug -- a plain dict would
    silently keep the last one and mis-route a team -- so we raise here instead.
    A label repeated within one team, or an accent variant landing on the same
    id ("leviatán"/"leviatan" -> 2359), is harmless and allowed.
    """
    mapping: dict[str, int] = {}
    for team in teams:
        for label in (team["name"], *team.get("aliases", ())):
            key = label.lower()
            existing = mapping.get(key)
            if existing is not None and existing != team["team_id"]:
                raise ValueError(
                    f"registry label collision: {key!r} maps to both "
                    f"{existing} and {team['team_id']}"
                )
            mapping[key] = team["team_id"]
    return mapping


# Canonical + alias lookup and a single compiled matcher, both built once at
# import so the request path never rebuilds them.
_NAME_TO_ID = _build_name_to_id(TRACKED_TEAMS)

# One alternation over every label (canonical names + aliases), longest-first
# so a longer name ("Team Liquid") is tried before a shorter alias ("TL"). The
# \b anchors both ends: word-boundary matching is why a short alias like "GE"
# can't false-match inside "change" or "damage".
_TEAM_PATTERN = re.compile(
    r"\b(" + "|".join(
        re.escape(name) for name in sorted(_NAME_TO_ID, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)


def teams_mentioned(text: str) -> set[int]:
    """Every tracked team whose canonical name appears in `text`.

    Case-insensitive, word-boundary matched. Returns a set of team_ids,
    empty when no tracked team is named. The caller resolves the MOVE
    decision from the count: exactly one -> switch the anchor to it; zero
    or many -> hold the current anchor.
    """
    return {
        _NAME_TO_ID[match.group(1).lower()]
        for match in _TEAM_PATTERN.finditer(text)
    }


if __name__ == "__main__":
    print(f"{len(TRACKED_TEAMS)} teams tracked:")
    for team in TRACKED_TEAMS:
        print(f"  {team['team_id']:<6} {team['region']:<10} {team['name']}")