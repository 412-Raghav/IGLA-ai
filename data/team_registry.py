"""Registry of Valorant teams IGLA tracks — the single source of truth.

Every team_id here is verified from vlr.gg via search_teams (never
guessed) and confirmed to be the active MAIN roster, not an Academy,
GC, Youth, or inactive squad. ingest.py loops over this registry to
scrape live tendencies. The README coverage list is derived from it.

To add a team: resolve its real team_id with vlr.search.search_teams,
confirm the roster with vlr.teams.roster, then append a row. To scale
to the full VCT scene, see the coverage-expansion guide.
"""

TRACKED_TEAMS = [
    # Americas
    {"team_id": 2, "name": "Sentinels", "region": "Americas"},
    {"team_id": 11058, "name": "G2 Esports", "region": "Americas"},
    {"team_id": 2359, "name": "LEVIATÁN", "region": "Americas"},
    # EMEA
    {"team_id": 2593, "name": "Fnatic", "region": "EMEA"},
    {"team_id": 2059, "name": "Team Vitality", "region": "EMEA"},
    {"team_id": 474, "name": "Team Liquid", "region": "EMEA"},
    # Pacific
    {"team_id": 624, "name": "Paper Rex", "region": "Pacific"},
    {"team_id": 918, "name": "Global Esports", "region": "Pacific"},
    {"team_id": 8185, "name": "DRX", "region": "Pacific"},  # vlr lists as "KIWOOM DRX" (title sponsor)
    # China
    {"team_id": 1120, "name": "EDward Gaming", "region": "China"},
    {"team_id": 13581, "name": "Xi Lai Gaming", "region": "China"},
    {"team_id": 11328, "name": "FunPlus Phoenix", "region": "China"},
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


if __name__ == "__main__":
    print(f"{len(TRACKED_TEAMS)} teams tracked:")
    for team in TRACKED_TEAMS:
        print(f"  {team['team_id']:<6} {team['region']:<10} {team['name']}")