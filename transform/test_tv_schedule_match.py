#!/usr/bin/env python3
"""Smoke tests for tv_schedule_match.py -- team-name reconciliation between
fbschedules.com's naming and CFBD's raw naming, and the outlet overlay
dict/unmatched-row split match_outlets() returns.
"""

import pandas as pd

from tv_schedule_match import match_outlets

GAMES_DF = pd.DataFrame([
    {"id": 101, "awayTeam": "Massachusetts", "homeTeam": "Rutgers"},  # CFBD's raw name differs from fbschedules' "UMass"
    {"id": 102, "awayTeam": "Akron", "homeTeam": "Wake Forest"},      # exact match on both sides
])

TV_DF = pd.DataFrame([
    {"away_team": "UMass", "home_team": "Rutgers", "network": "BTN"},
    {"away_team": "Akron", "home_team": "Wake Forest", "network": "ACCN"},
    {"away_team": "Nobody State", "home_team": "Nowhere Tech", "network": "ESPN+"},  # no matching CFBD game
])


def test_resolves_display_name_alias():
    outlets, _ = match_outlets(GAMES_DF, TV_DF)
    assert outlets[101] == "BTN", "fbschedules' 'UMass' should resolve to CFBD's 'Massachusetts' via TEAM_NAME_SUBSTITUTIONS"


def test_exact_name_match():
    outlets, _ = match_outlets(GAMES_DF, TV_DF)
    assert outlets[102] == "ACCN"


def test_unmatched_row_is_reported_not_dropped_silently():
    outlets, unmatched = match_outlets(GAMES_DF, TV_DF)
    assert len(outlets) == 2, f"expected exactly 2 matched games, got {outlets}"
    assert len(unmatched) == 1
    assert unmatched[0]["away_team"] == "Nobody State"


def test_empty_inputs_dont_error():
    outlets, unmatched = match_outlets(GAMES_DF, pd.DataFrame(columns=["away_team", "home_team", "network"]))
    assert outlets == {}
    assert unmatched == []

    outlets, unmatched = match_outlets(pd.DataFrame(columns=["id", "awayTeam", "homeTeam"]), TV_DF)
    assert outlets == {}
    assert len(unmatched) == len(TV_DF)


if __name__ == "__main__":
    test_resolves_display_name_alias()
    test_exact_name_match()
    test_unmatched_row_is_reported_not_dropped_silently()
    test_empty_inputs_dont_error()
    print("All tv_schedule_match tests passed.")
