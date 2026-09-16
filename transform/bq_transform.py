"""Merge/format logic for turning raw BigQuery rows into final grid game documents.

Ported from cfb-grid-python/mern/python/v2/generate_sched.py. The BigQuery
raw_* tables preserve the CFBD API's original camelCase field names, so this
is largely a straight port -- the main differences from the legacy script are
noted inline: records/rankings/all_games arrive already flattened by
bq_reader.py instead of via pd.json_normalize, and get_team_schedule reads a
single camelCase shape instead of the legacy dual snake_case/camelCase
fallback (BigQuery only ever produces one shape).
"""

import os
import re
from collections import defaultdict
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

import tv_schedule_match
from config.networks import NETWORK_COLUMNS, NETWORK_DISPLAY_MAPPINGS
from config.teams import TEAM_NAME_SUBSTITUTIONS
from time_utils import get_time_window, adjust_datetime_for_timezone

TIMEZONES = ['E', 'C', 'M', 'P', 'A', 'H']


def load_colors() -> Dict[int, str]:
    """Load team colors from the colors.csv bundled into the image."""
    colors_path = os.path.join(os.path.dirname(__file__), 'colors.csv')
    colors = {}
    with open(colors_path) as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) >= 3:
                team_id = int(os.path.basename(parts[1]).replace('.png', ''))
                colors[team_id] = parts[2]
    return colors


def get_team_schedule(all_games: List[dict], team_name: str) -> List[Dict[str, Any]]:
    """Build a team's full-season schedule from the raw_all_games snapshot."""
    schedule = []
    for game in all_games:
        week = game.get('week')
        start_date = game.get('startDate')
        date = str(start_date)[:10] if start_date is not None else ''
        home_team = TEAM_NAME_SUBSTITUTIONS.get(game.get('homeTeam'), game.get('homeTeam'))
        away_team = TEAM_NAME_SUBSTITUTIONS.get(game.get('awayTeam'), game.get('awayTeam'))
        neutral_site = bool(game.get('neutralSite', False))
        home_points = game.get('homePoints')
        away_points = game.get('awayPoints')
        # Both sides have to be present to call a result -- CFBD occasionally
        # reports only one team's score for a given game, and comparing an
        # int to a missing None raises rather than just meaning "no result
        # yet" like a fully-unscored game does.
        has_score = home_points is not None and away_points is not None

        if team_name == home_team:
            schedule.append({
                'week': week,
                'date': date,
                'opponent': away_team,
                'location': 'neutral' if neutral_site else 'home',
                'result': ('W' if home_points > away_points else 'L') if has_score else None,
                'score': f"{home_points} - {away_points}" if has_score else None
            })
        elif team_name == away_team:
            schedule.append({
                'week': week,
                'date': date,
                'opponent': home_team,
                'location': 'neutral' if neutral_site else 'away',
                'result': ('W' if away_points > home_points else 'L') if has_score else None,
                'score': f"{away_points} - {home_points}" if has_score else None
            })
    schedule.sort(key=lambda g: (g['date'], g['week'] if g['week'] is not None else 0))
    return schedule


def _normalize_network_name(value):
    """Applies the same NETWORK_DISPLAY_MAPPINGS taxonomy CFBD's outlet names
    use to fbschedules.com's network strings, which additionally include
    simulcast combos (e.g. 'NBC/Peacock', 'TNT/HBO Max') -- the primary
    broadcast network (first segment) is what NETWORK_COLUMNS keys on."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    primary = str(value).split('/')[0].strip()
    return NETWORK_DISPLAY_MAPPINGS.get(primary, primary)


_FB_TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})\s*(am|pm)$', re.IGNORECASE)
_FB_DATE_RE = re.compile(r'^[A-Za-z]+,\s*([A-Za-z]+)\s+(\d{1,2})$')

_EASTERN_TZ = ZoneInfo('America/New_York')


def _parse_fbschedules_time(raw: Optional[str]) -> Optional[Tuple[int, int]]:
    """Parses fbschedules.com's 'H:MMam/pm' time string (always Eastern) into
    (hour24, minute). Returns None for anything that doesn't match (e.g. a
    literal 'TBD' entry, which means fbschedules doesn't know the time either)."""
    if not raw:
        return None
    match = _FB_TIME_RE.match(raw.strip())
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3).lower()
    if meridiem == 'pm' and hour != 12:
        hour += 12
    elif meridiem == 'am' and hour == 12:
        hour = 0
    return hour, minute


def _parse_fbschedules_date(raw: Optional[str], year: int):
    """Parses fbschedules.com's 'Weekday, Month Day' date header (e.g.
    'Saturday, November 7') into a date object for the given season year.
    Returns None for anything that doesn't match."""
    if not raw:
        return None
    match = _FB_DATE_RE.match(raw.strip())
    if not match:
        return None
    month_name, day = match.group(1), match.group(2)
    try:
        return datetime.strptime(f'{month_name} {day} {year}', '%B %d %Y').date()
    except ValueError:
        return None


def _apply_fbschedules_schedule(df: pd.DataFrame, fb_dates: Dict[int, str], fb_times: Dict[int, str]) -> pd.DataFrame:
    """fbschedules.com is the source of truth for kickoff date/time -- CFBD's
    startDate is used only as a fallback, per field, wherever fbschedules
    doesn't have a usable value for that game:

    - No parseable fbschedules time (missing, or a literal 'TBD') -> leave
      CFBD's startDate untouched entirely, since there's nothing to combine.
    - A parseable time but no parseable date -> keep CFBD's own Eastern
      calendar date, replacing just the time-of-day (this is also what
      recovers CFBD's startTimeTBD placeholder games -- CFBD marks those with
      a midnight-Eastern startDate, e.g. 2026 Ohio Dominican @ Morehead State,
      converts to 12:00 AM Eastern while fbschedules already had the real
      6:00pm kickoff).
    - Both parseable -> fbschedules' date and time replace CFBD's entirely.

    Runs for every matched game regardless of whether CFBD already had a
    non-midnight startDate -- fbschedules wins outright, not just as a
    TBD-placeholder filler."""
    if not fb_times or df.empty:
        return df

    df = df.copy()
    for idx, row in df.iterrows():
        game_id = row['id']
        parsed_time = _parse_fbschedules_time(fb_times.get(game_id))
        if parsed_time is None:
            continue  # nothing usable from fbschedules -- keep CFBD's startDate as-is

        et_dt = adjust_datetime_for_timezone(row['startDate'], 'E')
        parsed_date = _parse_fbschedules_date(fb_dates.get(game_id), int(row['season']))
        base_date = parsed_date if parsed_date is not None else et_dt.date()

        hour, minute = parsed_time
        corrected_et = datetime.combine(base_date, time(hour=hour, minute=minute))
        corrected_utc = corrected_et.replace(tzinfo=_EASTERN_TZ).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
        df.at[idx, 'startDate'] = pd.Timestamp(corrected_utc, tz='UTC')

    return df


def _split_off_week_zero(df: pd.DataFrame) -> pd.DataFrame:
    """CFBD has no separate "week 0" -- season-opener games (Aug 27-30) are
    folded into its week=1 alongside the following weekend, the same
    fbschedules-week-0-vs-1 conflation documented in CLAUDE.md. The grid
    displays those openers as their own week 0, so any week=1 game whose
    actual Eastern kickoff date falls in that Aug 27-30 window gets its
    'week' overridden to 0 here.

    Runs after _apply_fbschedules_schedule (called earlier in
    process_and_merge_dataframes) so it sees the corrected startDate, not
    CFBD's raw one -- CFBD's own date is sometimes wrong or a
    startTimeTBD midnight placeholder for exactly these early games."""
    if df.empty:
        return df
    df = df.copy()
    for idx, row in df.iterrows():
        if int(row['week']) != 1:
            continue
        et_date = adjust_datetime_for_timezone(row['startDate'], 'E').date()
        if et_date.month == 8 and 27 <= et_date.day <= 30:
            df.at[idx, 'week'] = 0
    return df


def process_and_merge_dataframes(games_df: pd.DataFrame, media_df: pd.DataFrame, tv_df: pd.DataFrame,
                                  venues_df: pd.DataFrame, rankings_df: pd.DataFrame,
                                  records_df: pd.DataFrame, win_prob_df: pd.DataFrame) -> tuple:
    """Merge and process all dataframes into a single comprehensive dataset.

    Returns (result_df, tv_schedule_unmatched_rows)."""
    result = games_df[['id', 'season', 'week', 'startDate', 'venueId',
                        'awayTeam', 'homeTeam', 'awayId', 'homeId',
                        'awayPoints', 'homePoints', 'awayLineScores',
                        'homeLineScores', 'completed',
                        'awayClassification', 'awayConference',
                        'homeClassification', 'homeConference', 'seasonType']]

    # Media data (CFBD games/media) -- primary outlet source
    media = media_df[['id', 'outlet']]
    result = pd.merge(result, media, on='id', how='left')

    # fbschedules.com TV schedule -- source of truth for outlet/date/time,
    # since it has broader coverage than CFBD's media endpoint and CFBD's
    # startDate is sometimes wrong or a startTimeTBD placeholder. CFBD's own
    # outlet/startDate is only kept as a fallback for whichever games/fields
    # fbschedules didn't match or didn't have a parseable value for (see
    # tv_schedule_match.py and _apply_fbschedules_schedule).
    if not tv_df.empty:
        tv_df = tv_df.assign(network=tv_df['network'].map(_normalize_network_name))
        outlet_overrides, fb_times, fb_dates, unmatched_tv_rows = tv_schedule_match.match_outlets(games_df, tv_df)
        result['outlet'] = result['id'].map(outlet_overrides).combine_first(result['outlet'])
        result = _apply_fbschedules_schedule(result, fb_dates, fb_times)
    else:
        unmatched_tv_rows = []

    result = _split_off_week_zero(result)

    # Venue data (inner join -- a game with no matching venue is dropped, matching legacy behavior)
    venues = venues_df[['id', 'name', 'city', 'state', 'countryCode']]
    venues = venues.rename(columns={'id': 'venueId', 'name': 'venueName'})
    result = pd.merge(result, venues, on='venueId', how='inner')

    # Rankings data -- only present once the "AP Top 25" poll exists for the week (never an error if it doesn't)
    if not rankings_df.empty:
        away_rankings = rankings_df.rename(columns={'rank': 'awayRank', 'school': 'awayTeam'})
        home_rankings = rankings_df.rename(columns={'rank': 'homeRank', 'school': 'homeTeam'})
        result = pd.merge(result, away_rankings, on='awayTeam', how='left')
        result = pd.merge(result, home_rankings, on='homeTeam', how='left')

    # Records data -- bq_reader already flattens total.wins/total.losses to wins/losses
    result = add_records_to_games(result, records_df)

    # Win probability data
    if not win_prob_df.empty:
        result = pd.merge(result, win_prob_df, left_on='id', right_on='gameId', how='left')

    return result, unmatched_tv_rows


def add_records_to_games(games_df: pd.DataFrame, records_df: pd.DataFrame) -> pd.DataFrame:
    """Add team records (formatted as '(W-L)') to the games DataFrame."""
    if records_df.empty:
        records_fmt = pd.DataFrame(columns=['team', 'record'])
    else:
        records_fmt = records_df.copy()
        records_fmt['record'] = (
            '(' + records_fmt['wins'].astype(str) + '-' + records_fmt['losses'].astype(str) + ')'
        )
        records_fmt = records_fmt[['team', 'record']]

    away_records = records_fmt.rename(columns={'team': 'awayTeam', 'record': 'awayRecord'})
    home_records = records_fmt.rename(columns={'team': 'homeTeam', 'record': 'homeRecord'})

    result = pd.merge(games_df, away_records, on='awayTeam', how='left')
    result = pd.merge(result, home_records, on='homeTeam', how='left')

    result = result.assign(
        awayRecord=lambda x: x['awayRecord'].fillna('(0-0)'),
        homeRecord=lambda x: x['homeRecord'].fillna('(0-0)')
    )

    return result


def apply_display_mappings(df: pd.DataFrame) -> pd.DataFrame:
    """Apply display name mappings for networks and teams."""
    for old, new in NETWORK_DISPLAY_MAPPINGS.items():
        df.loc[df.outlet == old, 'outlet'] = new

    for old, new in TEAM_NAME_SUBSTITUTIONS.items():
        df.loc[df.awayTeam == old, 'awayTeam'] = new
        df.loc[df.homeTeam == old, 'homeTeam'] = new

    return df


def add_time_information(df: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Add formatted time information to the DataFrame."""
    df = df.copy()
    df['dateTimeStr'] = df['startDate'].apply(lambda x: adjust_datetime_for_timezone(x, timezone))
    df['time'] = df['dateTimeStr'].dt.strftime('%H:%M')
    df['date'] = df['dateTimeStr'].dt.strftime('%Y-%m-%d')
    df['printTime'] = df['dateTimeStr'].dt.strftime('%I:%M %p')
    return df


def _as_list(val):
    """Convert BigQuery REPEATED-field values (list-like) to plain Python lists.

    Uses .tolist() rather than list() when available (numpy arrays) so the
    elements themselves come back as native Python int/float, not
    numpy.int64/float64 -- pymongo's BSON encoder has no codec for numpy
    scalar types and raises on them."""
    if val is None:
        return None
    if hasattr(val, 'tolist'):
        return val.tolist()
    try:
        return list(val)
    except TypeError:
        return val


def format_game_data(game: pd.Series, start_window, timezone: str, all_games: List[dict]) -> Dict[str, Any]:
    """Format a single game's data into the required MongoDB document structure."""

    def clean_value(val):
        # BigQuery REPEATED columns (e.g. line scores) come through as a
        # list/array rather than a scalar -- pd.isna() on one of those
        # returns an array of bools, not a single bool, which blows up the
        # `if` below with "truth value of an array is ambiguous" once real
        # (non-empty) values start showing up. Treat empty list-likes as
        # null instead of routing them through pd.isna().
        if hasattr(val, '__len__') and not isinstance(val, (str, bytes)):
            return val if len(val) > 0 else None
        if pd.isna(val):
            return None
        # pandas Series scalars come back as numpy types (int64/float64/bool_)
        # for columns that were ever null -- pymongo's BSON encoder has no
        # codec for those and raises, so normalize to native Python types.
        return val.item() if hasattr(val, 'item') else val

    away_team = TEAM_NAME_SUBSTITUTIONS.get(game['awayTeam'], game['awayTeam'])
    home_team = TEAM_NAME_SUBSTITUTIONS.get(game['homeTeam'], game['homeTeam'])

    away_team_schedule = get_team_schedule(all_games, away_team)
    home_team_schedule = get_team_schedule(all_games, home_team)

    outlet = clean_value(game.get('outlet'))
    column = NETWORK_COLUMNS.get(outlet) if outlet in NETWORK_COLUMNS else None

    print_time = clean_value(game.get('printTime'))
    formatted_time = print_time.lstrip('0') if isinstance(print_time, str) else None

    return {
        'game_id': int(game['id']),
        'col': column,
        'start': start_window,
        'season': int(game['season']),
        'week': int(game['week']),
        'date': game['date'],
        'time': formatted_time,
        'outlet': outlet,
        'away_team': away_team,
        'home_team': home_team,
        'away_id': int(game['awayId']),
        'home_id': int(game['homeId']),
        'away_color': clean_value(game.get('awayColor')),
        'home_color': clean_value(game.get('homeColor')),
        'away_rank': clean_value(game.get('awayRank')),
        'home_rank': clean_value(game.get('homeRank')),
        'away_record': game['awayRecord'],
        'home_record': game['homeRecord'],
        'timezone': timezone,
        'venue_name': game['venueName'],
        'city': game['city'],
        'state': game['state'],
        'spread': clean_value(game.get('spread')),
        'away_points': clean_value(game['awayPoints']),
        'home_points': clean_value(game['homePoints']),
        'away_line_scores': _as_list(clean_value(game['awayLineScores'])),
        'home_line_scores': _as_list(clean_value(game['homeLineScores'])),
        'completed': bool(game['completed']),
        'home_win_probability': clean_value(game.get('homeWinProbability')),
        'away_classification': clean_value(game['awayClassification']),
        'away_conference': clean_value(game['awayConference']),
        'home_classification': clean_value(game['homeClassification']),
        'home_conference': clean_value(game['homeConference']),
        'away_team_schedule': away_team_schedule,
        'home_team_schedule': home_team_schedule,
        'season_type': game['seasonType']
    }


def resolve_btn_conflicts(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve conflicts where multiple BTN games are in the same time slot."""
    time_slots = defaultdict(list)
    for game in games:
        if game['outlet'] == 'BTN':
            time_slots[(game['date'], game['start'])].append(game)

    for slot_games in time_slots.values():
        if len(slot_games) > 1:
            for i, game in enumerate(slot_games):
                if i > 0:
                    game['col'] += 1

    return games


def adjust_close_start_times(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Nudge apart start times that are too close together within the same column/date."""
    col_date_games = defaultdict(list)
    for game in games:
        if game['col'] is None or game['col'] == 18:
            continue
        col_date_games[(game['col'], game['date'])].append(game)

    for game_list in col_date_games.values():
        if len(game_list) <= 1:
            continue
        game_list.sort(key=lambda x: x['start'])
        for i in range(len(game_list) - 1):
            current_game = game_list[i]
            next_game = game_list[i + 1]
            start_diff = next_game['start'] - current_game['start']
            if 0 < start_diff < 6:
                next_game['start'] = current_game['start'] + 6

    return games


def generate_game_list(df: pd.DataFrame, timezone: str, all_games: List[dict]) -> List[Dict[str, Any]]:
    """Generate the final game list for a timezone, with conflict resolution applied.

    Games with no TV outlet (neither CFBD nor fbschedules had one), or whose
    outlet doesn't map to a grid column (NETWORK_COLUMNS has no entry for
    it), are dropped entirely -- MongoDB should only ever hold games that can
    actually be placed on the grid."""
    game_list = []
    for _, game in df.iterrows():
        if pd.isna(game.get('outlet')):
            continue

        start_window = None
        if pd.notna(game.get('time')):
            start_window = get_time_window(game['time'])
        formatted = format_game_data(game, start_window, timezone, all_games)
        if formatted['col'] is None:
            continue
        game_list.append(formatted)

    game_list = resolve_btn_conflicts(game_list)
    game_list = adjust_close_start_times(game_list)
    return game_list


def build_merged_dataframe(games_df, media_df, tv_df, venues_df, rankings_df, records_df, win_prob_df, colors) -> tuple:
    """Timezone-independent merge + display-mapping + color steps, run once per request.

    Returns (merged_df, tv_schedule_unmatched_rows)."""
    if games_df.empty:
        raise ValueError("No games returned for the requested year/week -- check raw_games ingestion.")

    merged, unmatched_tv_rows = process_and_merge_dataframes(
        games_df, media_df, tv_df, venues_df, rankings_df, records_df, win_prob_df
    )
    merged = apply_display_mappings(merged)
    merged['awayColor'] = merged['awayId'].map(colors)
    merged['homeColor'] = merged['homeId'].map(colors)
    return merged, unmatched_tv_rows


def generate_all_timezones(merged_df: pd.DataFrame, all_games: List[dict]) -> Dict[str, List[Dict[str, Any]]]:
    """Run the timezone-dependent steps (time formatting + grid layout) for all 6 timezones."""
    results = {}
    for timezone in TIMEZONES:
        tz_df = add_time_information(merged_df, timezone)
        results[timezone] = generate_game_list(tz_df, timezone, all_games)
    return results
