"""Live tactical-intel ingestion from vlr.gg via vlrdevapi.

One document per active roster player, summarizing recent agent
tendencies. Deterministic templating (no LLM): facts in, facts out.
Claude reasons over the retrieved facts at query time, not here.
"""

import datetime as dt
import logging

import vlrdevapi as vlr

logger = logging.getLogger("igla")

DEFAULT_TIMESPAN = "90d"


def build_player_document(team, player, stats, timespan):
    """Render one player's recent agent tendencies as a tactical document."""
    lines = [
        f"{team.tag} — {player.ign} ({player.real_name}), {player.role}. "
        f"Nationality: {player.country}."
    ]
    if stats:
        lines.append(f"Recent agent tendencies (last {timespan}):")
        for a in stats:
            lines.append(
                f"- {a.agent.capitalize()}: {round(a.usage_percent * 100)}% of maps "
                f"({a.usage_count} maps, {a.rounds_played} rounds). "
                f"{a.rating} rating, {a.acs} ACS, {a.kd} K/D, {a.adr} ADR, "
                f"{round(a.kast * 100)}% KAST. Entry: {a.fkpr} first kills/round, "
                f"{a.fdpr} first deaths/round."
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
            "role": player.role,
            "timespan": timespan,
            "agents": ", ".join(a.agent for a in stats) if stats else "",
            "ingested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    }


def fetch_team_tendencies(team_id, timespan=DEFAULT_TIMESPAN):
    """Fetch live agent tendencies for one team's active roster.

    Returns a list of document dicts. Lets exceptions propagate so the
    caller (ingest.py) decides whether to skip this team and continue.
    """
    team = vlr.teams.info(team_id=team_id)
    roster = vlr.teams.roster(team_id=team_id)
    players = [m for m in roster if m.role == "Player"]

    docs = [
        build_player_document(
            team, p, vlr.players.agent_stats(player_id=p.player_id, timespan=timespan), timespan
        )
        for p in players
    ]
    logger.info("Fetched %d player docs for %s (team_id=%s)", len(docs), team.tag, team_id)
    return docs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for doc in fetch_team_tendencies(team_id=624):  # Paper Rex
        print(doc["id"], "→", doc["metadata"]["agents"])
        print(doc["text"])
        print("-" * 60)