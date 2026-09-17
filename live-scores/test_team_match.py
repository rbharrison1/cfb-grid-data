#!/usr/bin/env python3
"""Smoke tests for team_match.py -- NCAA scoreboard team-name resolution
against Mongo's stored away_team/home_team, and the live-field extraction
from a matched NCAA game dict.
"""

from team_match import NCAA_NAME_ALIASES, match_ncaa_games


def _ncaa_game(away_short, home_short, away_score="7", home_score="14",
                period="FINAL", clock="0:00", state="final", title=None):
    return {
        "title": title or f"{away_short} {home_short}",
        "away": {"score": away_score, "names": {"short": away_short}},
        "home": {"score": home_score, "names": {"short": home_short}},
        "currentPeriod": period,
        "contestClock": clock,
        "gameState": state,
    }


def test_exact_name_match():
    # Mongo already stores "Miami (FL)" -- NCAA's names.short matches verbatim.
    lookup = {("Florida A&M", "Miami (FL)"): 101}
    games = [_ncaa_game("Florida A&M", "Miami (FL)")]
    matched, unmatched = match_ncaa_games(games, lookup)
    assert unmatched == []
    assert len(matched) == 1
    assert matched[0]["game_id"] == 101


def test_resolves_via_team_name_substitutions():
    # NCAA sends CFBD's raw "Ohio State"; Mongo's away_team/home_team are
    # always already the TEAM_NAME_SUBSTITUTIONS-mapped display form
    # ("Ohio St."), never the raw CFBD name -- this is the exception case
    # the substitution tier exists for.
    lookup = {("Ohio St.", "Michigan"): 202}
    games = [_ncaa_game("Ohio State", "Michigan")]
    matched, unmatched = match_ncaa_games(games, lookup)
    assert unmatched == []
    assert matched[0]["game_id"] == 202


def test_resolves_via_ncaa_name_aliases():
    NCAA_NAME_ALIASES["NCAA Weird Name"] = "Some Team"
    try:
        lookup = {("Some Team", "Other Team"): 303}
        games = [_ncaa_game("NCAA Weird Name", "Other Team")]
        matched, unmatched = match_ncaa_games(games, lookup)
        assert unmatched == []
        assert matched[0]["game_id"] == 303
    finally:
        del NCAA_NAME_ALIASES["NCAA Weird Name"]


def test_unmatched_game_is_skipped_not_guessed():
    lookup = {("Florida A&M", "Miami (FL)"): 101}
    games = [_ncaa_game("Nobody State", "Nowhere Tech")]
    matched, unmatched = match_ncaa_games(games, lookup)
    assert matched == []
    assert len(unmatched) == 1
    assert unmatched[0]["title"] == "Nobody State Nowhere Tech"


def test_live_fields_extracted_from_matched_game():
    lookup = {("Florida A&M", "Miami (FL)"): 101}
    games = [_ncaa_game("Florida A&M", "Miami (FL)", away_score="7", home_score="77",
                         period="FINAL", clock="0:00", state="final")]
    matched, _ = match_ncaa_games(games, lookup)
    game = matched[0]
    assert game["live_away_points"] == 7
    assert game["live_home_points"] == 77
    assert game["live_period"] == "FINAL"
    assert game["live_clock"] == "0:00"
    assert game["live_status"] == "final"
    assert game["live_updated_at"] is not None


def test_unparseable_score_becomes_none_not_an_error():
    lookup = {("Florida A&M", "Miami (FL)"): 101}
    games = [_ncaa_game("Florida A&M", "Miami (FL)", away_score="", home_score="")]
    matched, _ = match_ncaa_games(games, lookup)
    assert matched[0]["live_away_points"] is None
    assert matched[0]["live_home_points"] is None


def test_reversed_away_home_pair_still_matches():
    # NCAA and Mongo occasionally disagree on which side is "home" for the
    # same real game (confirmed by hand: NCAA's "Howard Alabama A&M" vs
    # Mongo's stored ('Alabama A&M', 'Howard')) -- the pair should still
    # resolve to the same game_id regardless of orientation.
    lookup = {("Alabama A&M", "Howard"): 404}
    games = [_ncaa_game("Howard", "Alabama A&M")]
    matched, unmatched = match_ncaa_games(games, lookup)
    assert unmatched == []
    assert matched[0]["game_id"] == 404


def test_pair_disambiguates_a_team_playing_at_a_different_site():
    # Two different games in the same week, sharing one team name -- the
    # (away, home) pair, not either name alone, must resolve each correctly.
    lookup = {
        ("Florida A&M", "Miami (FL)"): 101,
        ("Miami (FL)", "Florida State"): 202,
    }
    games = [
        _ncaa_game("Florida A&M", "Miami (FL)"),
        _ncaa_game("Miami (FL)", "Florida State"),
    ]
    matched, unmatched = match_ncaa_games(games, lookup)
    assert unmatched == []
    assert {m["game_id"] for m in matched} == {101, 202}


if __name__ == "__main__":
    test_exact_name_match()
    test_resolves_via_team_name_substitutions()
    test_resolves_via_ncaa_name_aliases()
    test_unmatched_game_is_skipped_not_guessed()
    test_live_fields_extracted_from_matched_game()
    test_unparseable_score_becomes_none_not_an_error()
    test_reversed_away_home_pair_still_matches()
    test_pair_disambiguates_a_team_playing_at_a_different_site()
    print("All team_match tests passed.")
