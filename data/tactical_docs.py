# IGLA Tactical Knowledge Base
# This is the private intel that makes IGLA specific
# In a real product — this comes from analyst VOD breakdowns,
# match data APIs, and scouting reports

TACTICAL_DOCUMENTS = [
    {
        "id": "prx_general_style",
        "text": """PRX (Paper Rex) General Playstyle:
        PRX are known for extremely aggressive, high-tempo play.
        They prioritize fast executes and early aggression over
        slow methodical setups. PRX rarely defaults — they commit
        hard and fast. Their philosophy is to overwhelm opponents
        with speed before setups can be established.
        Key tendency: PRX almost always execute before 60 seconds
        remaining on the clock. They hate slow rounds.""",
        "metadata": {"team": "PRX", "category": "general", "map": "all", "team_id": 624}
    },
    {
        "id": "prx_lotus_a_execute",
        "text": """PRX A Site Execute on Lotus:
        PRX consistently runs a fast A execute on Lotus.
        Their Jett player pushes A main aggressively using dash
        to reach A rubble early. The team follows immediately
        with a Gekko Wingman plant and Viper wall cutting off
        C link rotation. PRX commits all 5 to A when running
        this execute — no split. Counter: Early Killjoy lockdown
        on A site forces them to play through utility.
        Sage wall on A main delays their Jett entry.""",
        "metadata": {"team": "PRX", "category": "execute", "map": "lotus", "team_id": 624}
    },
    {
        "id": "prx_haven_style",
        "text": """PRX on Haven:
        PRX dominates mid on Haven with aggressive triple mid control.
        Their initiator always flashes C long while Jett takes A short
        simultaneously. They use this dual pressure to force defenders
        off mid control. PRX rarely attacks B site Haven directly —
        B is always a fake to rotate defenders before swinging A or C.
        If PRX is attacking B on Haven — it is a decoy 90% of the time.
        Counter: Do not rotate B until spike is down.""",
        "metadata": {"team": "PRX", "category": "map_tendency", "map": "haven", "team_id": 624}
    },
    {
        "id": "prx_economy_habits",
        "text": """PRX Economy and Save Round Behavior:
        PRX almost never full saves. On save rounds they force buy
        Spectre and Shorties. They use save rounds aggressively —
        always hunting for picks rather than avoiding contact.
        PRX IGL will call an aggressive save round push to deny
        enemy economy rather than protecting their own guns.
        Counter on save rounds: Do not overcommit to pushes.
        Let PRX come to you — they always will.""",
        "metadata": {"team": "PRX", "category": "economy", "map": "all", "team_id": 624}
    },
    {
        "id": "fnc_defensive_style",
        "text": """FNC (Fnatic) Defensive Philosophy:
        Fnatic under Boaster run structured, disciplined defenses.
        They almost always default first — gathering information
        before committing. Boaster prioritizes utility conservation
        for retakes over early aggression. FNC rarely sends more
        than 2 players to contest early aggression.
        Key strength: FNC retakes are among the best in the world.
        They trust their retake ability so they play further back
        than most teams on defense.""",
        "metadata": {"team": "FNC", "category": "general", "map": "all", "team_id": 2593}
    },
    {
        "id": "fnc_ascent_defense",
        "text": """FNC Defending on Ascent:
        FNC runs a 2-1-2 default on Ascent defense.
        Derke holds aggressive mid angles while two players
        anchor each site. Boaster stacks A site when opponents
        show A heavy early rounds. FNC Killjoy always places
        Nanoswarm on default plant — not aggressive positions.
        FNC's weakness on Ascent defense: B site when Killjoy
        is rotating. Fast B executes before KJ can reposition
        have a high success rate against FNC historically.""",
        "metadata": {"team": "FNC", "category": "defense", "map": "ascent", "team_id": 2593}
    },
    {
        "id": "sage_killjoy_combo",
        "text": """Sage and Killjoy Defensive Combination:
        Sage and Killjoy is one of the strongest defensive duos.
        Optimal setup: KJ Lockdown centered on site, Alarmbot
        on default plant, Turret watching main entry.
        Sage walls the primary entry point to delay 3-4 seconds.
        Sage holds off-angle behind the wall — not at the wall.
        When Lockdown activates: Sage wall should be broken by now
        forcing enemies into Lockdown radius without wall cover.
        Sage ult priority: Save for KJ if KJ dies before Lockdown pops.
        A living KJ with Lockdown beats a 5-stack execute 60% of the time.""",
        "metadata": {"team": "general", "category": "agent_combo", "map": "all", "scope": "general"}
    },
    {
        "id": "low_time_defense",
        "text": """Low Time Round Defense (Under 30 seconds):
        When defenders have under 30 seconds with spike unplanted:
        Enemy must plant and it must detonate — time is your weapon.
        Priority 1: Do not die for free — every dead defender
        removes a retake body.
        Priority 2: Force enemies to plant in suboptimal positions
        using utility. A rushed plant under pressure is often
        a bad plant defenders can retake.
        Priority 3: Stall entry with throwable utility — mollies,
        slow orbs, Sage wall. Each second of delay reduces
        enemy post-plant time.
        If outnumbered: Stall, do not fight. Let clock work for you.""",
        "metadata": {"team": "general", "category": "timing", "map": "all", "scope": "general"}
    }
]