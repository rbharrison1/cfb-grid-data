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
"""

from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from config.teams import TEAM_NAME_SUBSTITUTIONS


def _display_name_lookup(raw_names: Set[str]) -> Dict[str, str]:
    """Maps a TEAM_NAME_SUBSTITUTIONS display name back to whichever raw CFBD
    name is actually present in this week's games_df (scoping to the current
    week's actual teams avoids collisions from old aliases, e.g. 'Dixie
    State' and 'Utah Tech' both mapping to the same display name)."""
    return {TEAM_NAME_SUBSTITUTIONS.get(name, name): name for name in raw_names}


def _resolve_cfbd_name(name: str, raw_names: Set[str], display_lookup: Dict[str, str]) -> Optional[str]:
    if name in raw_names:
        return name
    return display_lookup.get(name)


def match_outlets(games_df: pd.DataFrame, tv_df: pd.DataFrame) -> Tuple[Dict[int, str], List[dict]]:
    """Returns ({gameId: network}, [unmatched tv_df rows as dicts]) for the
    given (already week-scoped) games_df and tv_df."""
    if tv_df.empty or games_df.empty:
        return {}, ([] if tv_df.empty else tv_df.to_dict("records"))

    raw_names = set(games_df["awayTeam"]) | set(games_df["homeTeam"])
    display_lookup = _display_name_lookup(raw_names)
    game_lookup = {(row.awayTeam, row.homeTeam): row.id for row in games_df.itertuples()}

    outlets: Dict[int, str] = {}
    unmatched: List[dict] = []

    for row in tv_df.itertuples():
        away = _resolve_cfbd_name(row.away_team, raw_names, display_lookup)
        home = _resolve_cfbd_name(row.home_team, raw_names, display_lookup)
        game_id = game_lookup.get((away, home)) if away and home else None

        if game_id is None or not row.network:
            unmatched.append(row._asdict())
            continue

        outlets[game_id] = row.network

    return outlets, unmatched
