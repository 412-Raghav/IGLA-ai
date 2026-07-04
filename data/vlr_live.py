"""Live tactical-intel ingestion from vlr.gg via vlrdevapi.

One document per active roster player, summarizing recent agent
tendencies. Deterministic templating (no LLM): facts in, facts out.
Claude reasons over the retrieved facts at query time, not here.
"""

import datetime as dt
import logging

import vlrdevapi as vlr

from rag.agent_roles import derive_player_role, role_phrase

logger = logging.getLogger("igla")

DEFAULT_TIMESPAN = "90d"


def _pct(value):
    """Format a 0-1 stat as a percent, or 'N/A' if vlr.gg has no data."""
    return f"{round(value * 100)}%" if value is not None else "N/A"


def _num(value):
    """Format a raw stat as-is, or 'N/A' if vlr.gg has no data."""
    return value if value is not None else "N/A"


def build_player_document(team, player, stats, timespan):
    """Render one player's recent agent tendencies as a tactical document."""
    role = role_phrase(derive_player_role(stats))
    lines = [
        f"{team.tag} — {player.ign} ({player.real_name}), {player.role}. "
        f"Role: {role}. "
        f"Nationality: {player.country}."
    ]
    if stats:
        lines.append(f"Recent agent tendencies (last {timespan}):")
        for a in stats:
            lines.append(
                f"- {a.agent.capitalize()}: {_pct(a.usage_percent)} of maps "
                f"({_num(a.usage_count)} maps, {_num(a.rounds_played)} rounds). "
                f"{_num(a.rating)} rating, {_num(a.acs)} ACS, {_num(a.kd)} K/D, "
                f"{_num(a.adr)} ADR, {_pct(a.kast)} KAST. "
                f"Entry: {_num(a.fkpr)} first kills/round, "
                f"{_num(a.fdpr)} first deaths/round."
            )
    else:
        lines.append(
            f"No agent data in the last {timespan} (possibly inactive or benched)."
        )

    return {
        "id": f"vlr-player-{player.player_id}",
        "text": "\n".join(lines),
        "metadata": {
            "source": "vlr.gg",
            "team": team.name,
            "team_tag": team.tag,
            "team_id": team.team_id,
            "player": player.ign,
            "player_id": player.player_id,
            "role": derive_player_role(stats),
            "timespan": timespan,
            "agents": ", ".join(a.agent for a in stats) if stats else "",
            "ingested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    }


def fetch_team_roster_stats(team_id, timespan=DEFAULT_TIMESPAN):
    """Fetch one team's active roster with each player's raw agent stats.

    Returns (team, roster_stats), where roster_stats is a list of
    (player, stats) pairs. This is the raw material BOTH consumers read:
    fetch_team_tendencies (live-ingest templating) and the build-time
    brief-assembler in data/generate_docs.py -- so both draw from ONE
    fetch path and can never drift apart.

    Lets exceptions propagate so the caller decides whether to skip.
    """
    team = vlr.teams.info(team_id=team_id)
    roster = vlr.teams.roster(team_id=team_id)
    players = [m for m in roster if m.role == "Player"]
    roster_stats = [
        (p, vlr.players.agent_stats(player_id=p.player_id, timespan=timespan))
        for p in players
    ]
    return team, roster_stats


def fetch_team_tendencies(team_id, timespan=DEFAULT_TIMESPAN):
    """Fetch live agent tendencies for one team's active roster.

    Returns a list of document dicts. Lets exceptions propagate so the
    caller (ingest.py) decides whether to skip this team and continue.
    """
    team, roster_stats = fetch_team_roster_stats(team_id, timespan)
    docs = [
        build_player_document(team, p, stats, timespan)
        for p, stats in roster_stats
    ]
    logger.info(
        "Fetched %d player docs for %s (team_id=%s)", len(docs), team.tag, team_id
    )
    return docs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for doc in fetch_team_tendencies(team_id=624):  # Paper Rex
        print(doc["id"], "→", doc["metadata"]["agents"])
        print(doc["text"])
        print("-" * 60)