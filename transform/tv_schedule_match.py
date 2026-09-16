"""Matches fbschedules.com TV-schedule rows to CFBD games by team name.

fbschedules.com shares no id with CFBD, so games are matched by
(away_team, home_team) within the target week. bq_reader.py already scopes
both games_df and tv_df to the same (year, week) before this runs, so the
team-name pair alone is enough to disambiguate -- a team can't play the same
opponent twice in one week, so no separate date check is needed.

Team names differ between the two sources (fbschedules tends to use CFBD's
short/common name, e.g. "UMass" vs CFBD's official "Massachusetts"), so both
sides are reconciled through config.teams.TEAM_NAME_SUBSTITUTIONS, which
already maps CFBD's raw names to those short display names elsewhere in this
pipeline.

FBSCHEDULES_NAME_ALIASES below covers naming quirks specific to this one
source that neither CFBD's raw name nor TEAM_NAME_SUBSTITUTIONS' display name
catches -- abbreviations (e.g. "NIU" for Northern Illinois), dropped "State"
(e.g. "Southeast Missouri" for CFBD's "Southeast Missouri State"), and at
least one plain-ASCII vs. accented-character mismatch ("San Jose State" vs.
CFBD's "San José State"). Built against live 2026 week-1 and week-2 scrapes,
where these accounted for every otherwise-unmatched game in both weeks;
extend as new mismatches turn up in later weeks.
"""

from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from config.teams import TEAM_NAME_SUBSTITUTIONS

FBSCHEDULES_NAME_ALIASES = {
    "Appalachian State": "App State",
    "CCSU": "Central Connecticut",
    "Franklin College": "Franklin",
    "HCU": "Houston Christian",
    "Miami (Ohio)": "Miami (OH)",
    "Grambling State": "Grambling",
    "Miles College": "Miles",
    "NIU": "Northern Illinois",
    "Pitt": "Pittsburgh",
    "SC State": "South Carolina State",
    "San Jose State": "San José State",
    "Southeast Missouri": "Southeast Missouri State",
    "Southeastern La.": "SE Louisiana",
    "Southern Conn.": "Southern Connecticut State",
    "St. Thomas": "St. Thomas (MN)",
    "UIW": "Incarnate Word",
    "Virginia-Lynchburg": "Virginia Lynchburg",
    "UAPB": "Arkansas-Pine Bluff",
    "WKU": "Western Kentucky",
    "Webber Intl.": "Webber International",
}


def _display_name_lookup(raw_names: Set[str]) -> Dict[str, str]:
    """Maps a TEAM_NAME_SUBSTITUTIONS display name back to whichever raw CFBD
    name is actually present in this week's games_df (scoping to the current
    week's actual teams avoids collisions from old aliases, e.g. 'Dixie
    State' and 'Utah Tech' both mapping to the same display name)."""
    return {TEAM_NAME_SUBSTITUTIONS.get(name, name): name for name in raw_names}


def _resolve_cfbd_name(name: str, raw_names: Set[str], display_lookup: Dict[str, str]) -> Optional[str]:
    if name in raw_names:
        return name
    if name in display_lookup:
        return display_lookup[name]
    return FBSCHEDULES_NAME_ALIASES.get(name)


def match_outlets(
    games_df: pd.DataFrame, tv_df: pd.DataFrame
) -> Tuple[Dict[int, str], Dict[int, str], Dict[int, str], List[dict]]:
    """Returns ({gameId: network}, {gameId: raw fbschedules time string},
    {gameId: raw fbschedules date string}, [unmatched tv_df rows as dicts])
    for the given (already week-scoped) games_df and tv_df.

    A row only lands in `unmatched` if its team names couldn't be resolved to
    a CFBD game at all, or it resolved but had neither a network, a time, nor
    a date to contribute -- a matched row missing one or two of those three
    still populates whichever dict applies."""
    if tv_df.empty or games_df.empty:
        return {}, {}, {}, ([] if tv_df.empty else tv_df.to_dict("records"))

    raw_names = set(games_df["awayTeam"]) | set(games_df["homeTeam"])
    display_lookup = _display_name_lookup(raw_names)
    game_lookup = {(row.awayTeam, row.homeTeam): row.id for row in games_df.itertuples()}

    outlets: Dict[int, str] = {}
    fb_times: Dict[int, str] = {}
    fb_dates: Dict[int, str] = {}
    unmatched: List[dict] = []

    for row in tv_df.itertuples():
        away = _resolve_cfbd_name(row.away_team, raw_names, display_lookup)
        home = _resolve_cfbd_name(row.home_team, raw_names, display_lookup)
        game_id = game_lookup.get((away, home)) if away and home else None

        if game_id is None:
            unmatched.append(row._asdict())
            continue

        if row.network:
            outlets[game_id] = row.network
        if row.time:
            fb_times[game_id] = row.time
        if row.date:
            fb_dates[game_id] = row.date

        if not row.network and not row.time and not row.date:
            unmatched.append(row._asdict())

    return outlets, fb_times, fb_dates, unmatched
