#!/usr/bin/env python3
"""Smoke tests for current_week.py -- deriving the "current" (season, week)
slot(s) from a Mongo collection's stored `date`/`timezone` fields, without a
real MongoDB connection.
"""

from datetime import date

from current_week import current_weeks


class FakeCollection:
    """Minimal stand-in for pymongo.collection.Collection.find(), just
    enough to exercise current_weeks()'s filter/projection usage."""

    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection):
        lo, hi = query["date"]["$gte"], query["date"]["$lte"]
        tz = query["timezone"]
        return [
            {"season": d["season"], "week": d["week"]}
            for d in self._docs
            if d["timezone"] == tz and lo <= d["date"] <= hi
        ]


def _doc(season, week, date_str, timezone="E"):
    return {"season": season, "week": week, "date": date_str, "timezone": timezone}


def test_finds_the_week_containing_today():
    docs = [_doc(2026, 3, "2026-09-19")]
    collection = FakeCollection(docs)
    assert current_weeks(collection, date(2026, 9, 19)) == [(2026, 3)]


def test_lookback_catches_a_late_thursday_night_game_that_rolled_to_friday():
    docs = [_doc(2026, 3, "2026-09-18")]  # Thursday night game
    collection = FakeCollection(docs)
    # Polling on Friday should still catch Thursday's date via LOOKBACK_DAYS.
    assert current_weeks(collection, date(2026, 9, 19)) == [(2026, 3)]


def test_lookahead_catches_saturdays_games_when_polled_thursday():
    docs = [_doc(2026, 3, "2026-09-19")]  # Saturday
    collection = FakeCollection(docs)
    # Polling on Thursday should already see Saturday's week via LOOKAHEAD_DAYS.
    assert current_weeks(collection, date(2026, 9, 17)) == [(2026, 3)]


def test_dedupes_multiple_games_in_the_same_week():
    # Two different games in the same week shouldn't produce two copies of
    # the same (season, week) pair.
    docs = [_doc(2026, 3, "2026-09-19"), _doc(2026, 3, "2026-09-18")]
    collection = FakeCollection(docs)
    assert current_weeks(collection, date(2026, 9, 19)) == [(2026, 3)]


def test_multiple_weeks_at_a_boundary_are_both_returned():
    # Week 3's Monday-night game (09-21) and week 4's Thursday opener
    # (09-24) both fall inside the [today-1, today+2] window when polled
    # on the Tuesday in between.
    docs = [_doc(2026, 3, "2026-09-21"), _doc(2026, 4, "2026-09-24")]
    collection = FakeCollection(docs)
    result = current_weeks(collection, date(2026, 9, 22))
    assert result == [(2026, 3), (2026, 4)]


def test_no_games_in_window_returns_empty():
    docs = [_doc(2026, 3, "2026-09-19")]
    collection = FakeCollection(docs)
    assert current_weeks(collection, date(2026, 9, 1)) == []


if __name__ == "__main__":
    test_finds_the_week_containing_today()
    test_lookback_catches_a_late_thursday_night_game_that_rolled_to_friday()
    test_lookahead_catches_saturdays_games_when_polled_thursday()
    test_dedupes_multiple_games_in_the_same_week()
    test_multiple_weeks_at_a_boundary_are_both_returned()
    test_no_games_in_window_returns_empty()
    print("All current_week tests passed.")
