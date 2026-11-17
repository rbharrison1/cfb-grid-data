#!/usr/bin/env python3
"""GameModal fields on basketball documents: the last-5 / next-3 schedule
window, per-half line scores (finals only), and venue/context passthrough."""

from cbb_transform import ScheduleIndex, TeamResolver, build_documents, build_records

TEAMS = [
    {'id': 1, 'sourceId': '150', 'school': 'Duke', 'mascot': 'Blue Devils', 'conference': 'ACC', 'primaryColor': '003087'},
    {'id': 2, 'sourceId': '153', 'school': 'North Carolina', 'mascot': 'Tar Heels', 'conference': 'ACC', 'primaryColor': '7BAFD4'},
]


def _game(game_id, day, away_id, home_id, status='scheduled', away_pts=0, home_pts=0, **extra):
    names = {1: 'Duke', 2: 'North Carolina'}
    g = {
        'id': game_id, 'season': 2027, 'seasonType': 'regular', 'status': status,
        'startDate': f'2026-11-{day:02d}T23:00:00.000Z', 'startTimeTbd': False,
        'awayTeamId': away_id, 'awayTeam': names.get(away_id, f'Opponent {away_id}'),
        'homeTeamId': home_id, 'homeTeam': names.get(home_id, f'Opponent {home_id}'),
        'awayPoints': away_pts, 'homePoints': home_pts, 'neutralSite': False,
    }
    g.update(extra)
    return g


def _season():
    # Duke (id 1): 10 games, game 106 is the 7th.
    games = []
    for n in range(10):
        gid = 100 + n
        if n < 6:
            games.append(_game(gid, 1 + n, 1, 900 + n, status='final', away_pts=80, home_pts=70 + n * 3))
        elif n == 6:
            games.append(_game(gid, 1 + n, 1, 2))
        else:
            games.append(_game(gid, 1 + n, 900 + n, 1, neutralSite=(n == 8)))
    return games


def _index(games):
    return ScheduleIndex(games, TeamResolver(TEAMS))


def test_window_is_five_before_three_after():
    window = _index(_season()).window(1, 106)
    assert [e['game_id'] for e in window] == [101, 102, 103, 104, 105, 107, 108, 109]


def test_window_at_season_start_and_end():
    idx = _index(_season())
    assert [e['game_id'] for e in idx.window(1, 100)] == [101, 102, 103]
    assert [e['game_id'] for e in idx.window(1, 109)] == [104, 105, 106, 107, 108]


def test_entry_location_result_and_score():
    window = {e['game_id']: e for e in _index(_season()).window(1, 106)}
    assert window[101]['location'] == 'away'
    assert window[101]['result'] == 'W' and window[101]['score'] == '80 - 73'
    assert window[105]['result'] == 'L' and window[105]['score'] == '80 - 85'
    assert window[107]['location'] == 'home' and window[107]['result'] is None
    assert window[108]['location'] == 'neutral'
    assert window[101]['date'] == '2026-11-02'
    assert window[101]['opponent_color'] is None  # unknown non-D1 opponent
    unc = next(e for e in _index(_season()).window(1, 105) if e['game_id'] == 106)
    assert unc['opponent'] == 'North Carolina' and unc['opponent_color'].startswith('#')


def test_unknown_opponent_not_counted_by_schedule():
    resolver = TeamResolver(TEAMS)
    ScheduleIndex(_season(), resolver).window(1, 106)
    assert not resolver.unknown


def test_cancelled_games_left_out():
    games = _season()
    games[7]['status'] = 'cancelled'
    assert 107 not in [e['game_id'] for e in _index(games).window(1, 106)]


def test_records_only_count_finals():
    # Six finals: Duke 80 vs 70/73/76/79 (W) and 82/85 (L); scheduled games ignored.
    assert build_records(_season())[1] == '(4-2)'


def test_documents_carry_modal_fields():
    season = _season()
    final = _game(200, 20, 1, 2, status='final', away_pts=74, home_pts=79,
                  awayPeriodPoints=[38, 36], homePeriodPoints=[35, 44],
                  venue='Dean E. Smith Center', city='Chapel Hill', state='NC',
                  attendance=21750, conferenceGame=True, gameNotes='Rivalry', excitement=6.1)
    future = _game(201, 25, 2, 1, awayPeriodPoints=[0, 0], homePeriodPoints=[0, 0])
    season += [final, future]
    docs, _ = build_documents([final, future], {}, TEAMS, [], season)
    by_id = {d['game_id']: d for d in docs['2026-11-20'] + docs['2026-11-25'] if d['timezone'] == 'E'}

    f = by_id[200]
    assert f['away_line_scores'] == [38, 36] and f['home_line_scores'] == [35, 44]
    assert f['venue_name'] == 'Dean E. Smith Center' and f['city'] == 'Chapel Hill' and f['state'] == 'NC'
    assert f['attendance'] == 21750 and f['conference_game'] is True and f['notes'] == 'Rivalry'
    assert f['excitement'] == 6.1
    assert [e['game_id'] for e in f['away_team_schedule']][-3:] == [108, 109, 201]
    assert [e['game_id'] for e in f['home_team_schedule']] == [106, 201]

    u = by_id[201]
    assert u['away_line_scores'] is None and u['home_line_scores'] is None
    assert u['attendance'] is None and u['venue_name'] is None


def test_frozen_games_get_only_modal_fields_refreshed():
    from mongo_writer import frozen_refresh_ops
    docs = [
        {'game_id': 7, 'timezone': 'E', 'home_points': 80, 'away_record': '(1-0)', 'completed': True,
         'home_line_scores': [40, 40], 'venue_name': 'Arena', 'home_team_schedule': []},
        {'game_id': 7, 'timezone': 'P', 'home_points': 80, 'venue_name': 'Arena'},
        {'game_id': 8, 'timezone': 'E', 'venue_name': 'Other'},
    ]
    slot = {'season': 2027, 'slate_date': '2026-11-16'}
    ops = frozen_refresh_ops(docs, {7}, slot)
    assert [op._filter for op in ops] == [{**slot, 'game_id': 7, 'timezone': 'E'}, {**slot, 'game_id': 7, 'timezone': 'P'}]
    assert ops[0]._doc['$set'] == {'home_line_scores': [40, 40], 'venue_name': 'Arena', 'home_team_schedule': []}
