#!/usr/bin/env python3
"""Tests that full-season win_prob requests are split per week, since CFBD
truncates the season-wide metrics/wp/pregame response at 1000 rows."""

from unittest.mock import patch

import bq_ingest
from bq_ingest import _fetch_rows, FULL_SEASON_WEEK, FULL_SEASON_WEEKS


def test_full_season_win_prob_fetches_each_week():
    with patch.object(bq_ingest, "_fetch_endpoint", side_effect=lambda k, e, params: [params]) as fetch:
        rows = _fetch_rows("key", "win_prob", "metrics/wp/pregame", {"year": 2026}, FULL_SEASON_WEEK)
    assert fetch.call_count == len(FULL_SEASON_WEEKS)
    assert [r["week"] for r in rows] == list(FULL_SEASON_WEEKS)
    assert all(r["year"] == 2026 for r in rows)


def test_single_week_win_prob_is_one_call():
    with patch.object(bq_ingest, "_fetch_endpoint", return_value=[{"gameId": 1}]) as fetch:
        rows = _fetch_rows("key", "win_prob", "metrics/wp/pregame", {"year": 2026, "week": 11}, 11)
    fetch.assert_called_once_with("key", "metrics/wp/pregame", params={"year": 2026, "week": 11})
    assert rows == [{"gameId": 1}]


def test_full_season_other_keys_are_one_call():
    with patch.object(bq_ingest, "_fetch_endpoint", return_value=[]) as fetch:
        _fetch_rows("key", "games", "games", {"year": 2026}, FULL_SEASON_WEEK)
    fetch.assert_called_once_with("key", "games", params={"year": 2026})
