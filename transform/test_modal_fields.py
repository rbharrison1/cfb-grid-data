#!/usr/bin/env python3
"""The game-context fields cfb-grid-web's GameModal shows (neutral site,
conference game, attendance, excitement, notes) pass through from raw_games
to the Mongo document."""

import pandas as pd

from bq_transform import _as_int, add_time_information, generate_game_list


def _game(**extra):
    row = {
        'id': 1, 'season': 2026, 'week': 13, 'startDate': pd.Timestamp('2026-11-28T17:00:00Z'),
        'startTimeTBD': False, 'outlet': 'FOX', 'awayTeam': 'Away', 'homeTeam': 'Home',
        'awayId': 1, 'homeId': 2, 'awayPoints': 31, 'homePoints': 27,
        'awayLineScores': [7, 10, 7, 0, 7], 'homeLineScores': [3, 7, 7, 7, 3], 'completed': True,
        'awayClassification': 'fbs', 'awayConference': 'Big Ten',
        'homeClassification': 'fbs', 'homeConference': 'Big Ten', 'seasonType': 'regular',
        'awayRecord': '(11-1)', 'homeRecord': '(11-1)',
        'venueName': 'Stadium', 'city': 'City', 'state': 'ST',
    }
    row.update(extra)
    return row


def _doc(row):
    return generate_game_list(add_time_information(pd.DataFrame([row]), 'E'), 'E', [])[0]


def test_context_fields_pass_through():
    doc = _doc(_game(neutralSite=True, conferenceGame=True, attendance='110,245',
                     excitementIndex=7.9, notes='Big Ten Championship'))
    assert doc['neutral_site'] is True
    assert doc['conference_game'] is True
    assert doc['attendance'] == 110245
    assert doc['excitement'] == 7.9
    assert doc['notes'] == 'Big Ten Championship'
    assert doc['away_line_scores'] == [7, 10, 7, 0, 7]


def test_context_fields_missing_from_row():
    doc = _doc(_game())
    assert doc['neutral_site'] is False
    assert doc['conference_game'] is False
    assert doc['attendance'] is None
    assert doc['excitement'] is None
    assert doc['notes'] is None


def test_as_int():
    assert _as_int('105000') == 105000
    assert _as_int('0') is None
    assert _as_int('n/a') is None
    assert _as_int(None) is None


def test_schedule_dates_are_eastern():
    from bq_transform import get_team_schedule
    all_games = [
        # Sat 11/28 7:30 PM ET = Sun 00:30 UTC.
        {'id': 9, 'week': 14, 'startDate': pd.Timestamp('2026-11-29T00:30:00Z'), 'homeTeam': 'Home',
         'awayTeam': 'Away', 'neutralSite': False, 'homePoints': None, 'awayPoints': None},
        {'id': 10, 'week': 13, 'startDate': '2026-11-21T17:00:00.000Z', 'homeTeam': 'Away',
         'awayTeam': 'Other', 'neutralSite': False, 'homePoints': 20, 'awayPoints': 10},
    ]
    sched = get_team_schedule(all_games, 'Away')
    assert [(e['game_id'], e['date']) for e in sched] == [(10, '2026-11-21'), (9, '2026-11-28')]
    assert sched[0]['result'] == 'W' and sched[1]['location'] == 'away'


def test_frozen_games_get_only_modal_fields_refreshed():
    from mongo_writer import frozen_refresh_ops
    data = [
        {'game_id': 1, 'away_points': 99, 'completed': True, 'notes': 'Bowl', 'neutral_site': True,
         'away_team_schedule': [{'date': '2026-11-28'}]},
        {'game_id': 2, 'notes': 'not frozen'},
    ]
    slot = {'season': 2026, 'week': 13, 'timezone': 'E'}
    ops = frozen_refresh_ops(data, {1}, slot)
    assert len(ops) == 1
    assert ops[0]._filter == {**slot, 'game_id': 1}
    fields = ops[0]._doc['$set']
    assert fields == {'notes': 'Bowl', 'neutral_site': True, 'away_team_schedule': [{'date': '2026-11-28'}]}
    assert 'away_points' not in fields and 'completed' not in fields


def test_context_fields_survive_the_merge():
    # The merge keeps a fixed column list from games_df; the context fields
    # were once dropped there, so every document got False/None.
    from bq_transform import process_and_merge_dataframes
    games = pd.DataFrame([{
        'id': 1, 'season': 2026, 'week': 11, 'startDate': pd.Timestamp('2026-11-14T17:00:00Z'),
        'startTimeTBD': False, 'venueId': 5, 'awayTeam': 'Away', 'homeTeam': 'Home', 'awayId': 1, 'homeId': 2,
        'awayPoints': 10, 'homePoints': 20, 'awayLineScores': [10], 'homeLineScores': [20], 'completed': True,
        'awayClassification': 'fbs', 'awayConference': 'SEC', 'homeClassification': 'fbs',
        'homeConference': 'SEC', 'seasonType': 'regular',
        'neutralSite': True, 'conferenceGame': True, 'attendance': '90000', 'excitementIndex': 4.2, 'notes': 'Rivalry',
    }])
    media = pd.DataFrame([{'id': 1, 'outlet': 'ESPN'}])
    venues = pd.DataFrame([{'id': 5, 'name': 'Stadium', 'city': 'City', 'state': 'ST', 'countryCode': 'US'}])
    merged, _ = process_and_merge_dataframes(games, media, pd.DataFrame(), venues, pd.DataFrame(),
                                             pd.DataFrame(), pd.DataFrame())
    row = merged.iloc[0]
    assert bool(row['neutralSite']) and bool(row['conferenceGame'])
    assert row['excitementIndex'] == 4.2 and row['notes'] == 'Rivalry' and row['attendance'] == '90000'
