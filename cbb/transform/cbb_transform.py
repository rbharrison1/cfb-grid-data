"""Turns raw CBBD rows into per-timezone grid game documents.

Produces the same flat document shape cfb-grid-web's grid reads for football
(see format_game_data in transform/bq_transform.py), plus what its GameModal
shows: venue, line scores (per half, finals only), attendance, notes, and a
schedule *window* per team (the 5 games before this one and the 3 after,
not the whole 30-game season). No spread or win probability (not ingested).
Differences from football worth knowing:

- Basketball is navigated by date, not week. Every game gets a `slate_date`
  (its Eastern calendar date), which is also the Mongo write key -- the
  per-timezone `date` can differ for late West Coast/Hawaii tip-offs.
- A game takes GAME_SLOTS (4) half-hour rows on the grid, not football's 6;
  cfb-grid-web's CBB config uses the same value.
- Outlets with no grid column go to the streaming section instead of being
  dropped (see config/networks.py). Games with no broadcast at all are kept
  with outlet/col None, and TBD tip times get start None; cfb-grid-web lists
  both in its Not Televised & TBD section.
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
from ratings_match import RatingsLookup
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

    def resolve(self, team_id, name: str, count_unknown: bool = True) -> Dict[str, Any]:
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
        if count_unknown:
            self.unknown[name] += 1
        return {'name': name, 'id': None, 'color': None, 'conference': None, 'classification': None}


def build_records(season_games: List[dict]) -> Dict[int, str]:
    """CBBD team id -> '(W-L)' from every final game of the season so far."""
    wins, losses = Counter(), Counter()
    for g in season_games:
        if g.get('status') != 'final':
            continue
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


SCHEDULE_BEFORE = 5
SCHEDULE_AFTER = 3


class ScheduleIndex:
    """Each team's season, ordered by tip-off, so a game's document can carry
    the games around it (cfb-grid-web's modal shows "last 5 / next 3").
    Built from every season game, not just the [start, end] being written."""

    def __init__(self, season_games: List[dict], resolver: 'TeamResolver'):
        self.resolver = resolver
        self._cache = {}
        by_team = defaultdict(list)
        for g in season_games:
            if g.get('status') in SKIPPED_STATUSES or not g.get('startDate'):
                continue
            for side in ('away', 'home'):
                team_id = g.get(f'{side}TeamId')
                if team_id is not None:
                    by_team[team_id].append(g)
        self.by_team = {
            team_id: sorted(games, key=lambda g: (parse_utc(g['startDate']), g['id']))
            for team_id, games in by_team.items()
        }

    def window(self, team_id, game_id) -> Optional[List[Dict[str, Any]]]:
        # Same answer for all six timezone copies of a game; compute once.
        key = (team_id, game_id)
        if key not in self._cache:
            self._cache[key] = self._window(team_id, game_id)
        return self._cache[key]

    def _window(self, team_id, game_id) -> Optional[List[Dict[str, Any]]]:
        games = self.by_team.get(team_id)
        if not games:
            return None
        idx = next((i for i, g in enumerate(games) if g['id'] == game_id), None)
        if idx is None:
            return None
        picked = games[max(0, idx - SCHEDULE_BEFORE):idx] + games[idx + 1:idx + 1 + SCHEDULE_AFTER]
        return [self._entry(team_id, g) for g in picked]

    def _entry(self, team_id, g) -> Dict[str, Any]:
        side, opp = ('home', 'away') if g.get('homeTeamId') == team_id else ('away', 'home')
        opponent = self.resolver.resolve(g.get(f'{opp}TeamId'), g[f'{opp}Team'], count_unknown=False)
        mine, theirs = _points(g, side), _points(g, opp)
        has_score = mine is not None and theirs is not None and mine != theirs
        return {
            'game_id': int(g['id']),
            'date': slate_date_of(parse_utc(g['startDate'])).isoformat(),
            'opponent': opponent['name'],
            'opponent_id': opponent['id'],
            'opponent_color': opponent['color'],
            'location': 'neutral' if g.get('neutralSite') else side,
            'result': ('W' if mine > theirs else 'L') if has_score else None,
            'score': f"{mine} - {theirs}" if has_score else None,
        }


def _line_scores(game: dict, side: str) -> Optional[List[int]]:
    # Per-half points (plus any OTs), only once the game is final -- an
    # in-progress or future snapshot can carry partial or 0-filled arrays.
    if game.get('status') != 'final':
        return None
    periods = game.get(f'{side}PeriodPoints')
    return [int(p) for p in periods] if periods else None


def _points(game: dict, side: str):
    # CBBD reports 0-0 for games that haven't been played yet.
    return game.get(f'{side}Points') if game.get('status') == 'final' else None


def format_game(game, slate_date, outlet, col, away, home, ranks, records, timezone, start_utc,
                schedules: Optional['ScheduleIndex'] = None,
                ratings: Optional[RatingsLookup] = None) -> Dict[str, Any]:
    if game.get('startTimeTbd'):
        # CBBD's TBD placeholder is midnight Eastern; converting it would put
        # the game on the previous day west of Eastern. Keep the slate date,
        # with a 'TBD' time and no grid slot.
        local_date, time_str, start = slate_date.isoformat(), 'TBD', None
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
        # Game modal fields (cfb-grid-web GameModal).
        'away_line_scores': _line_scores(game, 'away'),
        'home_line_scores': _line_scores(game, 'home'),
        'venue_name': game.get('venue'),
        'city': game.get('city'),
        'state': game.get('state'),
        'conference_game': bool(game.get('conferenceGame')),
        'attendance': game.get('attendance') or None,
        'excitement': game.get('excitement'),
        'notes': game.get('gameNotes'),
        'away_team_schedule': schedules.window(game.get('awayTeamId'), game['id']) if schedules else None,
        'home_team_schedule': schedules.window(game.get('homeTeamId'), game['id']) if schedules else None,
        # T-Rank / NET as of this game's date (see ratings_match.py); the
        # modal's Stats tab reads <side>_stats.
        'away_stats': ratings.stats_for(game.get('awayTeamId'), slate_date) if ratings else None,
        'home_stats': ratings.stats_for(game.get('homeTeamId'), slate_date) if ratings else None,
        'ratings_as_of': ratings.as_of(slate_date) if ratings else None,
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
        if g['col'] not in (None, STREAMING_COL) and g['start'] is not None:
            by_slot[(g['date'], g['col'], g['start'])].append(g)
    for slot_games in by_slot.values():
        for i, g in enumerate(slot_games[1:], start=1):
            g['col'] = BTN_OVERFLOW_COL if g['outlet'] == 'BTN' and i == 1 else STREAMING_COL

    by_col = defaultdict(list)
    for g in games:
        if g['col'] not in (None, STREAMING_COL) and g['start'] is not None:
            by_col[(g['date'], g['col'])].append(g)
    for col_games in by_col.values():
        col_games.sort(key=lambda g: g['start'])
        for prev, nxt in zip(col_games, col_games[1:]):
            if nxt['start'] - prev['start'] < GAME_SLOTS:
                nxt['start'] = prev['start'] + GAME_SLOTS
    return games


def build_documents(games, media, cbbd_teams, rankings, season_games,
                    torvik_rows=(), net_rows=()) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """Returns ({slate_date 'YYYY-MM-DD': [docs for every timezone]}, stats).
    season_games is every game of the season (any status), for records and
    schedule windows. torvik_rows / net_rows are the ratings snapshots from
    bq_reader.read_torvik_snapshots / read_net_snapshots."""
    resolver = TeamResolver(cbbd_teams)
    records = build_records(season_games)
    schedules = ScheduleIndex(season_games, resolver)
    rank_lookup = RankLookup(rankings)
    ratings = RatingsLookup(list(torvik_rows), list(net_rows), cbbd_teams)
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

        start_utc = parse_utc(game['startDate'])
        slate_date = slate_date_of(start_utc)
        away = resolver.resolve(game.get('awayTeamId'), game['awayTeam'])
        home = resolver.resolve(game.get('homeTeamId'), game['homeTeam'])
        ranks = rank_lookup.ranks_on(slate_date)
        stats['games'] += 1
        for tz in TIMEZONES:
            per_tz[tz].append(format_game(game, slate_date, outlet, col, away, home, ranks, records, tz, start_utc,
                                          schedules, ratings))

    by_slate_date = defaultdict(list)
    for tz_games in per_tz.values():
        for doc in resolve_slot_conflicts(tz_games):
            by_slate_date[doc['slate_date']].append(doc)

    stats_out = dict(stats)
    stats_out['unmapped_outlets'] = dict(unmapped_outlets)
    stats_out['teams_without_id'] = dict(resolver.unknown)
    stats_out['ratings_unmatched'] = ratings.unmatched()
    return dict(by_slate_date), stats_out
