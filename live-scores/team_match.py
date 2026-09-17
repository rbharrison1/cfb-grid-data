"""Matches NCAA scoreboard entries to Mongo `games` documents by team name,
scoped to a season (not a week -- see build_game_lookup's docstring for why),
and extracts the live fields to patch.

NCAA's gameID shares no id space with CFBD/Mongo's game_id, so games are
matched by (away_team, home_team) -- mirrors transform/tv_schedule_match.py's
approach for reconciling fbschedules.com against CFBD: a team essentially
never plays the same opponent twice within one season (rare exception: a
regular-season game followed by a conference-championship rematch, which
this doesn't attempt to disambiguate -- accepted, rare edge case), so the
name pair alone identifies a game without needing a week to scope it.

Simpler than tv_schedule_match.py on one side: Mongo's stored away_team/
home_team are already CFBD names run through TEAM_NAME_SUBSTITUTIONS (see
transform/bq_transform.py's format_game_data), so there's no raw-vs-display
ambiguity on the Mongo side -- only on NCAA's. NCAA's `names.short` is often
already an exact match to that stored display form (verified by hand:
NCAA's "Florida A&M" / "Miami (FL)" both matched Mongo's stored form with no
mapping needed at all) -- TEAM_NAME_SUBSTITUTIONS and NCAA_NAME_ALIASES
below only need to cover the exceptions.

NCAA_NAME_ALIASES starts empty -- fill in empirically as real weeks are
polled and unmatched games get reviewed, same provenance as
transform/tv_schedule_match.py's FBSCHEDULES_NAME_ALIASES (built from actual
unmatched rows in live 2026 week-1/week-2 scrapes, not guessed upfront).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from config.teams import TEAM_NAME_SUBSTITUTIONS

NCAA_NAME_ALIASES: Dict[str, str] = {
    # "NCAA names.short value": "Mongo away_team/home_team value"
    # Seeded from a real 2026 week-2 games_test run (63/86 matched before
    # these were added, 23 unmatched -- all of these confirmed by that run).
    "South Fla.": "USF",
    "Western Ky.": "W. Kentucky",
    "Central Mich.": "C. Michigan",
    "Central Conn. St.": "C. Connecticut",
    "Eastern Mich.": "E. Michigan",
    "Northern Colo.": "N. Colorado",
    "Western Mich.": "W. Michigan",
    "Middle Tenn.": "MTSU",
    "NIU": "N. Illinois",
    "Southern U.": "Southern",
    "West Ga.": "West Georgia",
    "Western Caro.": "W. Carolina",
    "Western Ill.": "W. Illinois",
    "Southern Miss.": "Southern Miss",
    "Fla. Atlantic": "FAU",
    "Ga. Southern": "Georgia So.",
    "Texas Southern": "Texas So.",
    "North Dakota St.": "N. Dakota St.",
    "Sacramento St.": "Sac. State",
    "Southern California": "USC",
    # Second batch, seeded from the same 2026 week-2 run after adding the
    # FCS scoreboard feed (98/131 matched before these were added).
    "Army West Point": "Army",
    "Eastern Wash.": "E. Washington",
    "UNI": "N. Iowa",
    "Eastern Ky.": "E. Kentucky",
    "Northwestern St.": "NW State",
    "Eastern Ill.": "E. Illinois",
    "Lamar University": "Lamar",
    "Alcorn": "Alcorn St.",
    "SFA": "S.F. Austin",
    "N.C. A&T": "N. Carolina A&T",
    "N.C. Central": "NC Central",
    "Ky. Christian": "Kentucky Chr.",
    "Mississippi Val.": "MVSU",
    "UIW": "Incarnate Word",
    "Northern Ariz.": "N. Arizona",
    # Mongo actually stores "Virginia Lynchburg" (no hyphen) for this season
    # -- TEAM_NAME_SUBSTITUTIONS' 'Virginia University Of Lynchburg' ->
    # 'Va.-Lynchburg' entry didn't fire, meaning CFBD's current raw name for
    # this school doesn't match that (stale) dict key, so it passed through
    # unchanged. Confirmed via games_test: 'Virginia Lynchburg' is the real
    # stored value, not 'Va.-Lynchburg'.
    "Virginia-Lynchburg": "Virginia Lynchburg",
    "Central Ark.": "C. Arkansas",
    "Southeast Mo. St.": "SE Missouri St.",
    "Southern Ill.": "S. Illinois",
    "North Ala.": "North Alabama",
    "Southeastern La.": "SE Louisiana",
    # Third batch, seeded from a 2026 week-1 games_test run after switching
    # build_game_lookup to season-wide scope (each confirmed by checking the
    # team's actual Mongo matchup via the "mongo_pairs_involving_either_side"
    # debug field -- the exact reported pairing was present, just under a
    # different name spelling).
    "La. Christian": "Louisiana Christian",
    "Okla. Panhandle": "Oklahoma Panhandle",
    "Central Wash.": "C. Washington",
}


def build_game_lookup(collection, season: int) -> Dict[Tuple[str, str], int]:
    """(away_team, home_team) -> game_id for every doc in this season.

    Deliberately NOT scoped to a single Mongo week, even though the caller
    is polling one specific NCAA "week" number. Two independent, confirmed
    problems with trying to map NCAA's week number onto Mongo's week field
    directly:

    1. CFBD's own week=1 covers two fbschedules.com weeks, and
       bq_transform.py's _split_off_week_zero re-splits that CFBD week=1
       bucket into Mongo week=0 vs week=1 by Eastern kickoff date (Aug 27-30
       inclusive -> week 0, everything else stays week=1) -- see CLAUDE.md's
       gotcha. A single-week-scoped query would need to know to also check
       week=0 whenever week=1 is requested.

    2. Even accounting for that, NCAA's own "week 01" bucket turned out to
       be WIDER than a single Mongo week -- confirmed by hand (2026 week 1):
       NCAA reported "Richmond Howard" as a week-01 game (actual date
       09/05/2026), but Mongo's week=0/week=1 slot had Howard's actual grid
       game that week against Alabama A&M instead, with Richmond paired
       against Bucknell -- i.e. NCAA's week-01 response included at least
       one game that Mongo doesn't file under week 0 or week 1 at all. There
       is no reliable week-number mapping to special-case here; NCAA's
       bucketing doesn't line up with Mongo's on a fixed offset.

    Scoping by season instead of week sidesteps both problems entirely: a
    team essentially never plays the same opponent twice in one season (see
    module docstring), so the (away, home) pair alone still disambiguates
    without needing a week filter at all. Uses the (season, week, away_team,
    home_team) compound index (transform/ensure_indexes.py) via its leading
    `season` prefix; timezone='E' picks one of the 6 fanout docs per game to
    avoid duplicate keys with identical values.
    """
    docs = collection.find(
        {"season": season, "timezone": "E"},
        {"game_id": 1, "away_team": 1, "home_team": 1, "_id": 0},
    )
    # games_test is a shared, ad-hoc scratch collection (CLAUDE.md's testing
    # convention) -- unlike production `games`, it can hold stale/malformed
    # docs left over from unrelated test runs (confirmed: some lacked
    # game_id entirely once this query stopped being week-scoped and started
    # sweeping the whole season). Skip anything missing a required field
    # rather than letting one bad doc 500 the whole poll.
    return {
        (d["away_team"], d["home_team"]): d["game_id"]
        for d in docs
        if "game_id" in d and "away_team" in d and "home_team" in d
    }


def _resolve_mongo_name(ncaa_name: Optional[str], mongo_names: Set[str]) -> Optional[str]:
    if not ncaa_name:
        return None
    if ncaa_name in mongo_names:
        return ncaa_name
    substituted = TEAM_NAME_SUBSTITUTIONS.get(ncaa_name)
    if substituted in mongo_names:
        return substituted
    aliased = NCAA_NAME_ALIASES.get(ncaa_name)
    return aliased if aliased in mongo_names else None


def _safe_int(value) -> Optional[int]:
    """NCAA sends score as a string (and an empty string pregame) -- parse
    defensively rather than letting a blank/unexpected value blow up the
    whole poll."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_live_fields(game: dict) -> dict:
    return {
        "live_away_points": _safe_int(game.get("away", {}).get("score")),
        "live_home_points": _safe_int(game.get("home", {}).get("score")),
        "live_period": game.get("currentPeriod"),
        "live_clock": game.get("contestClock"),
        "live_status": game.get("gameState"),
        "live_updated_at": datetime.now(timezone.utc),
    }


def match_ncaa_games(
    ncaa_games: List[dict], game_lookup: Dict[Tuple[str, str], int]
) -> Tuple[List[dict], List[dict]]:
    """Returns (matched, unmatched).

    matched: [{'game_id': int, 'live_away_points': ..., ...}, ...]
    unmatched: raw NCAA game dicts that couldn't be resolved to a Mongo doc.

    Unmatched games are skipped, not guessed at -- mirrors
    tv_schedule_match.py's philosophy. Expected/benign causes: an FCS
    opponent, or a real NCAA game that simply has no Mongo doc because it
    had no outlet mapping (games with no outlet are dropped from Mongo
    entirely -- see CLAUDE.md's "games dropped if outlet is null" gotcha).
    """
    mongo_names = {name for pair in game_lookup for name in pair}
    matched: List[dict] = []
    unmatched: List[dict] = []

    for g in ncaa_games:
        away = _resolve_mongo_name(g.get("away", {}).get("names", {}).get("short"), mongo_names)
        home = _resolve_mongo_name(g.get("home", {}).get("names", {}).get("short"), mongo_names)

        game_id = None
        if away and home:
            game_id = game_lookup.get((away, home))
            if game_id is None:
                # NCAA and CFBD/fbschedules occasionally disagree on which
                # side is "home" for the same real game (confirmed by hand:
                # NCAA's "Howard Alabama A&M" vs Mongo's stored
                # ('Alabama A&M', 'Howard')) -- try the reversed pair before
                # giving up. Same real game either way, so the game_id is
                # correct regardless of which side each source calls home.
                game_id = game_lookup.get((home, away))

        if game_id is None:
            unmatched.append(g)
            continue

        matched.append({"game_id": game_id, **_parse_live_fields(g)})

    return matched, unmatched
