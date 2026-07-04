#Build-time strategy-doc generation for IGLA (Phase 9c).


from rag.agent_roles import derive_player_role, role_phrase


def _fmt_pct(value):
    """Render a 0-1 fraction as a percent, or 'N/A' when vlr.gg has no data."""
    return f"{round(value * 100)}%" if value is not None else "N/A"


def _weighted_stat(stats, field):
    """Usage-weighted average of one per-agent stat across a player's pool.

    Each agent row carries its own value for `field` (e.g. 'acs') and a
    `usage_percent` (0-1) saying how much of the player's play it covers.
    Weighting by usage collapses several agent rows into one representative
    number reflecting how the player ACTUALLY splits their time -- the same
    logic derive_player_role uses to pick a primary role, applied here to
    firepower instead of role.

    Rows missing either the value or the weight are SKIPPED, not counted as
    zero: absent data must not drag an average down. Returns None when no
    row carries usable data, so the caller can flag the gap honestly.

    getattr(a, field) reads the attribute whose NAME is the string `field`,
    so this one function serves 'acs', 'rating', etc. with no duplication.
    """
    numerator = 0.0
    weight = 0.0
    for a in stats:
        value = getattr(a, field)
        if value is None or a.usage_percent is None:
            continue
        numerator += value * a.usage_percent
        weight += a.usage_percent
    return numerator / weight if weight > 0 else None


def _player_profile(player, stats):
    """Reduce one player's raw stats to the contract-permitted facts only."""
    raw_role = derive_player_role(stats)
    primary_role = raw_role.replace(" / Flex", "")
    is_flex = raw_role != primary_role
    display_role = role_phrase(primary_role) + (" / Flex" if is_flex else "")

    return {
        "ign": player.ign,
        "real_name": player.real_name,
        "country": player.country,
        "primary_role": primary_role,
        "is_flex": is_flex,
        "display_role": display_role,
        "agents": [(a.agent.capitalize(), a.usage_percent) for a in stats],
        "weighted_acs": _weighted_stat(stats, "acs"),
        "weighted_rating": _weighted_stat(stats, "rating"),
        "sparse": not stats or _weighted_stat(stats, "acs") is None,
    }


def _composition_line(profiles):
    """Return (role tally, flex line) across the roster."""
    counts: dict[str, int] = {}
    flex_players = []
    for p in profiles:
        counts[p["primary_role"]] = counts.get(p["primary_role"], 0) + 1
        if p["is_flex"]:
            flex_players.append(p["ign"])

    tally = ", ".join(f"{role} ({n})" for role, n in sorted(counts.items()))
    if flex_players:
        flex = f"Flex players: {', '.join(flex_players)}."
    else:
        flex = "Flex players: none (all specialists)."
    return tally, flex


def assemble_team_brief(team, roster_stats, timespan):
    """Assemble the grounded fact brief for one team.

    team         -- vlr team object (.name, .tag, .team_id).
    roster_stats -- list of (player, stats) pairs; stats is the raw
                    agent_stats list for that player (may be empty).
    timespan     -- data window the stats cover, e.g. '90d'. REQUIRED,
                    never defaulted: the window is a material fact about the
                    data, so the caller must state it rather than let a wrong
                    label slip in silently.

    Returns the brief as a single string -- this exact text is what the
    generator model is later shown, and what a human reviews at the gate.
    """
    profiles = [_player_profile(pl, st) for pl, st in roster_stats]
    ranked = sorted(
        profiles,
        key=lambda p: p["weighted_acs"] if p["weighted_acs"] is not None else -1.0,
        reverse=True,
    )
    tally, flex = _composition_line(profiles)

    lines = [
        f"TEAM FACT BRIEF — {team.name} ({team.tag}) [team_id={team.team_id}]",
        f"Data window: last {timespan}. Source: vlr.gg agent-usage statistics.",
        "",
        "ROSTER ROLE COMPOSITION:",
        f"{len(profiles)} players analyzed. Roles: {tally}.",
        flex,
        "",
        "PER-PLAYER PROFILES (ordered by firepower):",
    ]

    for p in ranked:
        lines.append(
            f"- {p['ign']} ({p['real_name']}, {p['country']}) — {p['display_role']}."
        )
        if p["agents"]:
            pool = ", ".join(f"{name} {_fmt_pct(pct)}" for name, pct in p["agents"])
            lines.append(f"    Agent pool: {pool}.")
        acs = "N/A" if p["weighted_acs"] is None else round(p["weighted_acs"])
        rating = (
            "N/A" if p["weighted_rating"] is None else round(p["weighted_rating"], 2)
        )
        lines.append(f"    Firepower (usage-weighted): {acs} ACS, {rating} rating.")
        if p["sparse"]:
            lines.append(
                "    LIMITED DATA: little or no usable stat data in window "
                "(likely a new signing or substitute)."
            )

    lines.append("")
    lines.append("FIREPOWER RANKING (usage-weighted ACS, highest first):")
    rankable = [p for p in ranked if p["weighted_acs"] is not None]
    unrankable = [p for p in ranked if p["weighted_acs"] is None]
    for i, p in enumerate(rankable, start=1):
        lines.append(f"{i}. {p['ign']}: {round(p['weighted_acs'])} ACS.")
    for p in unrankable:
        lines.append(f"- {p['ign']}: insufficient data to rank.")

    return "\n".join(lines)


if __name__ == "__main__":
    from types import SimpleNamespace

    def stat(agent, usage, acs, rating):
        # Real vlr rows carry more fields (kd, adr, kast, fkpr, fdpr...);
        # the assembler intentionally reads only these four.
        return SimpleNamespace(
            agent=agent, usage_percent=usage, acs=acs, rating=rating
        )

    def player(ign, real_name, country, player_id):
        return SimpleNamespace(
            ign=ign, real_name=real_name, country=country, player_id=player_id
        )

    team = SimpleNamespace(name="Test Squad", tag="TST", team_id=99999)

    # Fixtures exercise every branch: a pure main, a flex, a no-data sub,
    # and a player we know the AGENT for but not the firepower (stats None).
    roster_stats = [
        (player("blaze", "Test One", "Testland", 1),
         [stat("jett", 0.80, 245, 1.18), stat("raze", 0.15, 210, 1.05)]),
        (player("hinge", "Test Two", "Testland", 2),
         [stat("viper", 0.40, 190, 1.02),
          stat("killjoy", 0.35, 205, 1.10),
          stat("sova", 0.25, 180, 0.95)]),
        (player("rookie", "Test Three", "Testland", 3), []),
        (player("ghost", "Test Four", "Testland", 4),
         [stat("omen", 0.90, None, None)]),
    ]

    print(assemble_team_brief(team, roster_stats, timespan="90d"))