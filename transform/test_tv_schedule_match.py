#!/usr/bin/env python3
"""Smoke tests for tv_schedule_match.py -- team-name reconciliation between
fbschedules.com's naming and CFBD's raw naming, and the outlet/time/date
overlay dicts plus unmatched-row list match_outlets() returns.
"""

import pandas as pd

from tv_schedule_match import match_outlets

GAMES_DF = pd.DataFrame([
    {"id": 101, "awayTeam": "Massachusetts", "homeTeam": "Rutgers"},  # CFBD's raw name differs from fbschedules' "UMass"
    {"id": 102, "awayTeam": "Akron", "homeTeam": "Wake Forest"},      # exact match on both sides
])

TV_DF = pd.DataFrame([
    {"away_team": "UMass", "home_team": "Rutgers", "network": "BTN", "time": "6:00pm", "date": "Saturday, November 7"},
    {"away_team": "Akron", "home_team": "Wake Forest", "network": "ACCN", "time": "7:00pm", "date": "Saturday, November 7"},
    {"away_team": "Nobody State", "home_team": "Nowhere Tech", "network": "ESPN+", "time": "8:00pm", "date": "Saturday, November 7"},  # no matching CFBD game
])


def test_resolves_display_name_alias():
    outlets, _, _, _ = match_outlets(GAMES_DF, TV_DF)
    assert outlets[101] == "BTN", "fbschedules' 'UMass' should resolve to CFBD's 'Massachusetts' via TEAM_NAME_SUBSTITUTIONS"


def test_exact_name_match():
    outlets, _, _, _ = match_outlets(GAMES_DF, TV_DF)
    assert outlets[102] == "ACCN"


def test_times_and_dates_are_matched_alongside_outlets():
    _, fb_times, fb_dates, _ = match_outlets(GAMES_DF, TV_DF)
    assert fb_times == {101: "6:00pm", 102: "7:00pm"}
    assert fb_dates == {101: "Saturday, November 7", 102: "Saturday, November 7"}


def test_unmatched_row_is_reported_not_dropped_silently():
    outlets, fb_times, fb_dates, unmatched = match_outlets(GAMES_DF, TV_DF)
    assert len(outlets) == 2, f"expected exactly 2 matched games, got {outlets}"
    assert len(fb_times) == 2
    assert len(fb_dates) == 2
    assert len(unmatched) == 1
    assert unmatched[0]["away_team"] == "Nobody State"


def test_matched_game_missing_only_network_still_yields_time_and_date():
    games_df = pd.DataFrame([{"id": 201, "awayTeam": "Akron", "homeTeam": "Wake Forest"}])
    tv_df = pd.DataFrame([{"away_team": "Akron", "home_team": "Wake Forest", "network": None, "time": "7:00pm", "date": "Saturday, November 7"}])
    outlets, fb_times, fb_dates, unmatched = match_outlets(games_df, tv_df)
    assert outlets == {}
    assert fb_times == {201: "7:00pm"}
    assert fb_dates == {201: "Saturday, November 7"}
    assert unmatched == [], "a row with a resolvable game and a usable time/date shouldn't be reported as unmatched"


def test_empty_inputs_dont_error():
    outlets, fb_times, fb_dates, unmatched = match_outlets(
        GAMES_DF, pd.DataFrame(columns=["away_team", "home_team", "network", "time", "date"])
    )
    assert outlets == {}
    assert fb_times == {}
    assert fb_dates == {}
    assert unmatched == []

    outlets, fb_times, fb_dates, unmatched = match_outlets(pd.DataFrame(columns=["id", "awayTeam", "homeTeam"]), TV_DF)
    assert outlets == {}
    assert fb_times == {}
    assert fb_dates == {}
    assert len(unmatched) == len(TV_DF)


if __name__ == "__main__":
    test_resolves_display_name_alias()
    test_exact_name_match()
    test_times_and_dates_are_matched_alongside_outlets()
    test_unmatched_row_is_reported_not_dropped_silently()
    test_matched_game_missing_only_network_still_yields_time_and_date()
    test_empty_inputs_dont_error()
    print("All tv_schedule_match tests passed.")
