"""Turns raw CBBD rows into per-timezone grid game documents.

Produces the same flat document shape cfb-grid-web's grid reads for football
(see format_game_data in transform/bq_transform.py), minus the fields only the
football GameModal uses (team schedules, line scores, venue, spread, win
probability). Differences from football worth knowing:

- Basketball is navigated by date, not week. Every game gets a `slate_date`
  (its Eastern calendar date), which is also the Mongo write key -- the
  per-timezone `date` can differ for late West Coast/Hawaii tip-offs.
- A game takes GAME_SLOTS (4) half-hour rows on the grid, not football's 6;
  cfb-grid-web's CBB config uses the same value.
- Outlets with no grid column go to the streaming section instead of being
  dropped (see config/networks.py).
- D1 team identity comes from CBBD's own /teams list (sourceId is the ESPN
  id, which is also the logo filename). team_data's teams.json is applied on
  top only when its ESPN id agrees -- its football-oriented aliases are not
  always right for basketball (e.g. 'Little Rock' is an alias of Arkansas
  Baptist there).
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.networks import (
    BTN_OVERFLOW_COL, IGNORED_BROADCAST_TYPES, KNOWN_STREAMING_OUTLETS,
    NETWORK_COLUMNS, NETWORK_DISPLAY_MAPPINGS, STREAMING_COL,
)
from config.teams import TEAM_NAME_SUBSTITUTIONS, TEAM_COLORS
from time_utils import adjust_datetime_for_timezone, get_time_window

TIMEZONES = ['E', 'C', 'M', 'P', 'A', 'H']
GAME_SLOTS = 4
EASTERN = ZoneInfo('America/New_York')

# Longer names than this overflow a 110px grid cell; see display_name().
MAX_NAME_LENGTH = 15

# Games in these states have no real tip-off to put on the grid.
SKIPPED_STATUSES = {'cancelled', 'postponed'}

with open(os.path.join(os.path.dirname(__file__), 'config', 'teams.json'), encoding='utf-8') as f:
    _TEAM_ESPN_IDS = {team['canonical_name']: team['espn_id'] for team in json.load(f)}


def parse_utc(value) -> datetime:
    """BigQuery's TO_JSON_STRING renders a TIMESTAMP as e.g.
    '2026-11-01T14:30:00Z' (or with fractional seconds)."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def slate_date_of(start_utc: datetime) -> date:
    return start_utc.astimezone(EASTERN).date()


def pick_outlet(broadcasts: List[dict]) -> Tuple[Optional[str], Optional[int], List[str]]:
    """Picks the one outlet a game is shown under: the first broadcast on a
    grid-column network, else the first non-radio broadcast (streaming
    section). Returns (outlet, col, unmapped_names)."""
    candidates = []
    for b in broadcasts:
        name = b.get('broadcastName')
        if not name or b.get('broadcastType') in IGNORED_BROADCAST_TYPES:
            continue
        candidates.append(NETWORK_DISPLAY_MAPPINGS.get(name, name))

    unmapped = [n for n in candidates if n not in NETWORK_COLUMNS and n not in KNOWN_STREAMING_OUTLETS]
    for name in candidates:
        if name in NETWORK_COLUMNS:
            return name, NETWORK_COLUMNS[name], unmapped
    if candidates:
        return candidates[0], STREAMING_COL, unmapped
    return None, None, unmapped


def display_name(cbbd_team: dict) -> str:
    """Grid name for a D1 team teams.json doesn't cover: the school name in
    the football grid's 'Florida St.' style, or CBBD's shortDisplayName when
    that's still too long (e.g. 'Cal State Northridge' -> 'CSU Northridge')."""
    name = re.sub(r' State$', ' St.', cbbd_team['school'])
    if len(name) > MAX_NAME_LENGTH and cbbd_team.get('shortDisplayName'):
        return cbbd_team['shortDisplayName']
    return name


class TeamResolver:
    """Maps a CBBD team (id + name) to display name, ESPN/logo id, color,
    conference, and classification."""

    def __init__(self, cbbd_teams: List[dict]):
        self.by_id = {t['id']: t for t in cbbd_teams}
        self.unknown = Counter()

    def resolve(self, team_id, name: str) -> Dict[str, Any]:
        canonical = TEAM_NAME_SUBSTITUTIONS.get(name)
        cbbd = self.by_id.get(team_id)

        if cbbd is not None:
            espn_id = int(cbbd['sourceId']) if cbbd.get('sourceId') else None
            if canonical is not None and _TEAM_ESPN_IDS.get(canonical) == espn_id:
                display, color = canonical, TEAM_COLORS.get(canonical)
            else:
                display, color = display_name(cbbd), None
            if not color and cbbd.get('primaryColor'):
                color = f"#{cbbd['primaryColor']}"
            return {'name': display, 'id': espn_id, 'color': color,
                    'conference': cbbd.get('conference'), 'classification': 'd1'}

        # Non-D1 opponent: CBBD has no team record, so teams.json (which
        # covers many D2/D3 football schools) is the only source of an id/color.
        if canonical is not None:
            return {'name': canonical, 'id': _TEAM_ESPN_IDS.get(canonical), 'color': TEAM_COLORS.get(canonical),
                    'conference': None, 'classification': None}
        self.unknown[name] += 1
        return {'name': name, 'id': None, 'color': None, 'conference': None, 'classification': None}


def build_records(final_games: List[dict]) -> Dict[int, str]:
    """CBBD team id -> '(W-L)' from every final game of the season so far."""
    wins, losses = Counter(), Counter()
    for g in final_games:
        home_pts, away_pts = g.get('homePoints'), g.get('awayPoints')
        if home_pts is None or away_pts is None or home_pts == away_pts:
            continue
        winner, loser = ('homeTeamId', 'awayTeamId') if home_pts > away_pts else ('awayTeamId', 'homeTeamId')
        wins[g[winner]] += 1
        losses[g[loser]] += 1
    return {team_id: f"({wins[team_id]}-{losses[team_id]})" for team_id in set(wins) | set(losses)}


class RankLookup:
    """AP rank as of a given date: the latest poll released on or before it.
    Football matches polls by week number; basketball has no week on its
    games, so the poll's pollDate is used instead."""

    def __init__(self, rankings: List[dict]):
        polls = defaultdict(dict)
        for r in rankings:
            polls[parse_utc(r['pollDate']).date()][r['teamId']] = int(r['ranking'])
        self.polls = sorted(polls.items())

    def ranks_on(self, on: date) -> Dict[int, int]:
        current = {}
        for poll_date, ranks in self.polls:
            if poll_date > on:
                break
            current = ranks
        return current


def _points(game: dict, side: str):
    # CBBD reports 0-0 for games that haven't been played yet.
    return game.get(f'{side}Points') if game.get('status') == 'final' else None


def format_game(game, slate_date, outlet, col, away, home, ranks, records, timezone, start_utc) -> Dict[str, Any]:
    if game.get('startTimeTbd'):
        # CBBD's TBD placeholder is midnight Eastern; converting it would put
        # the game on the previous day west of Eastern. Keep the slate date and
        # list it in the streaming section with a 'TBD' time.
        local_date, time_str, start, col = slate_date.isoformat(), 'TBD', None, STREAMING_COL
    else:
        local = adjust_datetime_for_timezone(start_utc, timezone)
        local_date = local.strftime('%Y-%m-%d')
        time_str = local.strftime('%I:%M %p').lstrip('0')
        start = get_time_window(local.strftime('%H:%M'))

    return {
        'game_id': int(game['id']),
        'col': col,
        'start': start,
        'season': int(game['season']),
        'slate_date': slate_date.isoformat(),
        'date': local_date,
        'time': time_str,
        'outlet': outlet,
        'away_team': away['name'],
        'home_team': home['name'],
        'away_id': away['id'],
        'home_id': home['id'],
        'away_color': away['color'],
        'home_color': home['color'],
        'away_rank': ranks.get(game['awayTeamId']),
        'home_rank': ranks.get(game['homeTeamId']),
        'away_record': records.get(game['awayTeamId'], '(0-0)'),
        'home_record': records.get(game['homeTeamId'], '(0-0)'),
        'timezone': timezone,
        'away_points': _points(game, 'away'),
        'home_points': _points(game, 'home'),
        'completed': game.get('status') == 'final',
        'away_conference': away['conference'],
        'home_conference': home['conference'],
        'away_classification': away['classification'],
        'home_classification': home['classification'],
        'neutral_site': bool(game.get('neutralSite')),
        'season_type': game.get('seasonType'),
    }


def resolve_slot_conflicts(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Two games in the same column at the exact same start (regional feeds)
    can't share a cell: a second BTN game moves to the BTN overflow column,
    any other network's to the streaming section, which keeps its real time.
    Then, like football's adjust_close_start_times, a game starting less than
    GAME_SLOTS after the previous one in its column is nudged down so the two
    don't overlap -- its printed time is unchanged."""
    by_slot = defaultdict(list)
    for g in games:
        if g['col'] != STREAMING_COL and g['start'] is not None:
            by_slot[(g['date'], g['col'], g['start'])].append(g)
    for slot_games in by_slot.values():
        for i, g in enumerate(slot_games[1:], start=1):
            g['col'] = BTN_OVERFLOW_COL if g['outlet'] == 'BTN' and i == 1 else STREAMING_COL

    by_col = defaultdict(list)
    for g in games:
        if g['col'] != STREAMING_COL and g['start'] is not None:
            by_col[(g['date'], g['col'])].append(g)
    for col_games in by_col.values():
        col_games.sort(key=lambda g: g['start'])
        for prev, nxt in zip(col_games, col_games[1:]):
            if nxt['start'] - prev['start'] < GAME_SLOTS:
                nxt['start'] = prev['start'] + GAME_SLOTS
    return games


def build_documents(games, media, cbbd_teams, rankings, final_games) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """Returns ({slate_date 'YYYY-MM-DD': [docs for every timezone]}, stats)."""
    resolver = TeamResolver(cbbd_teams)
    records = build_records(final_games)
    rank_lookup = RankLookup(rankings)
    stats = Counter()
    unmapped_outlets = Counter()

    per_tz = defaultdict(list)
    for game in games:
        if game.get('status') in SKIPPED_STATUSES:
            stats['skipped_status'] += 1
            continue
        outlet, col, unmapped = pick_outlet(media.get(game['id'], []))
        unmapped_outlets.update(unmapped)
        if outlet is None:
            stats['no_broadcast'] += 1
            continue

        start_utc = parse_utc(game['startDate'])
        slate_date = slate_date_of(start_utc)
        away = resolver.resolve(game.get('awayTeamId'), game['awayTeam'])
        home = resolver.resolve(game.get('homeTeamId'), game['homeTeam'])
        ranks = rank_lookup.ranks_on(slate_date)
        stats['games'] += 1
        for tz in TIMEZONES:
            per_tz[tz].append(format_game(game, slate_date, outlet, col, away, home, ranks, records, tz, start_utc))

    by_slate_date = defaultdict(list)
    for tz_games in per_tz.values():
        for doc in resolve_slot_conflicts(tz_games):
            by_slate_date[doc['slate_date']].append(doc)

    stats_out = dict(stats)
    stats_out['unmapped_outlets'] = dict(unmapped_outlets)
    stats_out['teams_without_id'] = dict(resolver.unknown)
    return dict(by_slate_date), stats_out
