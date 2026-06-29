"""Maps Valorant agents to roles, and derives a player's role from
their scraped agent-usage stats.

Agent -> role is a GAME-WIDE CONSTANT (Riot's official classes), so this
one table generalises to every team in Valorant, not just PRX/FNC. A
player's role is then DERIVED from which agents they actually play most,
read straight from the live-scraped tendencies -- never hand-assigned.
"""

# A player is "flex" if their dominant role accounts for LESS than this
# share of their map pool. Calibrated on real PRX data: pure mains sit
# ~85%+ (something 91%, invy 85%), flex players ~56-58% (d4v41, f0rsakeN).
# 0.70 is the clean valley between those two clusters.
FLEX_THRESHOLD = 0.70

# Riot's four official agent classes. Update when Riot ships a new agent.
AGENT_ROLES = {
    # Duelists (entry / self-sufficient fraggers)
    "Jett": "Duelist", "Raze": "Duelist", "Phoenix": "Duelist",
    "Reyna": "Duelist", "Yoru": "Duelist", "Neon": "Duelist",
    "Iso": "Duelist", "Waylay": "Duelist",
    # Initiators (info / set up the team)
    "Sova": "Initiator", "Breach": "Initiator", "Skye": "Initiator",
    "KAY/O": "Initiator", "Fade": "Initiator", "Gekko": "Initiator",
    "Tejo": "Initiator",
    # Controllers (smokes / map control)
    "Brimstone": "Controller", "Omen": "Controller", "Viper": "Controller",
    "Astra": "Controller", "Harbor": "Controller", "Clove": "Controller",
    # Sentinels (defensive anchors / lockdown)
    "Killjoy": "Sentinel", "Cypher": "Sentinel", "Sage": "Sentinel",
    "Chamber": "Sentinel", "Deadlock": "Sentinel", "Vyse": "Sentinel",
}

# Analyst jargon -> the role it refers to. Lets concept queries like
# "entry fragger" resolve to the same role word we stamp on the doc.
ROLE_SYNONYMS = {
    "Duelist": ["duelist", "entry fragger", "entry", "fragger"],
    "Initiator": ["initiator", "flasher", "info gatherer"],
    "Controller": ["controller", "smoker", "smokes"],
    "Sentinel": ["sentinel", "anchor", "lockdown"],
}



def role_for_agent(agent: str) -> str:
    """Look up one agent's role. Unknown agents degrade, never crash."""
    return AGENT_ROLES.get(agent, "Unknown")


def derive_player_role(agent_stats) -> str:
    """Derive a player's role by summing map-share per role bucket.

    Groups the player's agents by role, totals usage_percent within each
    bucket, and takes the biggest bucket as the primary role. If that
    bucket is below FLEX_THRESHOLD of total play, the player is spread
    across roles -> "<Primary> / Flex". This weights by HOW MUCH each
    agent is played, not just agent count, so a 40%-Viper main outranks
    an 18%-Killjoy pick.

    agent_stats: list of vlr stat objects, each with .agent and
    .usage_percent (a 0-1 fraction). Empty -> "Unknown".
    """
    if not agent_stats:
        return "Unknown"

    # Accumulate map-share into per-role buckets.
    role_share: dict[str, float] = {}
    for a in agent_stats:
        role = role_for_agent(a.agent.capitalize())
        if role == "Unknown":
            continue  # unmapped agent contributes no role signal
        role_share[role] = role_share.get(role, 0.0) + a.usage_percent

    if not role_share:
        return "Unknown"  # every agent was unmapped

    total_mapped = sum(role_share.values())
    primary_role = max(role_share, key=role_share.get)
    primary_share = role_share[primary_role] / total_mapped

    if primary_share < FLEX_THRESHOLD:
        return f"{primary_role} / Flex"
    return primary_role

def role_phrase(role: str) -> str:
    """Render a role with its primary analyst synonym for the doc text,
    e.g. 'Duelist' -> 'Duelist (entry fragger)'. Gives concept queries
    like 'entry fragger' a direct lexical anchor in the document.
    """
    synonyms = ROLE_SYNONYMS.get(role, [])
    if len(synonyms) >= 2:
        return f"{role} ({synonyms[1]})"
    return role