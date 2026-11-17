#!/usr/bin/env python3
"""Smoke tests for keeping no-outlet and TBD-time games in the transform
output -- cfb-grid-web lists them in its Not Televised & TBD section instead
of the grid. A game with an outlet that isn't a NETWORK_COLUMNS key is still
dropped.
"""

import pandas as pd

from bq_transform import add_time_information, generate_game_list, _apply_fbschedules_schedule


def _game(game_id, outlet, start_date, tbd=False):
    return {
        'id': game_id, 'season': 2026, 'week': 13, 'startDate': pd.Timestamp(start_date),
        'startTimeTBD': tbd, 'outlet': outlet, 'awayTeam': 'Away', 'homeTeam': 'Home',
        'awayId': 1, 'homeId': 2, 'awayPoints': None, 'homePoints': None,
        'awayLineScores': None, 'homeLineScores': None, 'completed': False,
        'awayClassification': 'fbs', 'awayConference': 'SEC',
        'homeClassification': 'fbs', 'homeConference': 'SEC', 'seasonType': 'regular',
        'awayRecord': '(0-0)', 'homeRecord': '(0-0)',
        'venueName': 'Stadium', 'city': 'City', 'state': 'ST',
    }


def _games_for(rows, timezone):
    return {g['game_id']: g for g in generate_game_list(add_time_information(pd.DataFrame(rows), timezone), timezone, [])}


def test_no_outlet_game_is_kept_with_null_col():
    games = _games_for([_game(1, None, '2026-11-28T19:00:00Z')], 'E')
    assert games[1]['outlet'] is None
    assert games[1]['col'] is None
    assert games[1]['start'] is not None
    assert games[1]['time'] == '2:00 PM'


def test_unmapped_outlet_is_still_dropped():
    games = _games_for([_game(1, 'Some Regional Feed', '2026-11-28T19:00:00Z')], 'E')
    assert games == {}


def test_tbd_game_keeps_eastern_date_everywhere_with_no_slot():
    # CFBD's TBD placeholder is midnight Eastern (05:00Z in November).
    rows = [_game(1, 'ESPN', '2026-11-28T05:00:00Z', tbd=True)]
    for tz in ['E', 'P', 'H']:
        game = _games_for(rows, tz)[1]
        assert game['date'] == '2026-11-28'
        assert game['time'] == 'TBD'
        assert game['start'] is None
        assert game['col'] == 5


def test_tbd_games_skip_close_start_and_btn_conflict_handling():
    rows = [
        _game(1, 'BTN', '2026-11-28T05:00:00Z', tbd=True),
        _game(2, 'BTN', '2026-11-28T05:00:00Z', tbd=True),
        _game(3, 'BTN', '2026-11-28T17:00:00Z'),
    ]
    games = _games_for(rows, 'E')
    assert games[1]['col'] == 11 and games[2]['col'] == 11
    assert games[1]['start'] is None and games[2]['start'] is None


def test_fbschedules_time_clears_tbd_flag():
    df = pd.DataFrame([{'id': 1, 'season': 2026, 'startDate': pd.Timestamp('2026-11-28T05:00:00Z'), 'startTimeTBD': True}])
    result = _apply_fbschedules_schedule(df, {1: 'Saturday, November 28'}, {1: '3:30pm'})
    assert not result.loc[0, 'startTimeTBD']
    assert result.loc[0, 'startDate'] == pd.Timestamp('2026-11-28T20:30:00Z')
