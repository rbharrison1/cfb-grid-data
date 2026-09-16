#!/usr/bin/env python3
"""Smoke tests for bq_transform's fbschedules-is-source-of-truth override --
fbschedules.com's date/time win over CFBD's startDate whenever fbschedules
has a parseable value, falling back to CFBD field-by-field otherwise. This
supersedes the older, narrower behavior where fbschedules only filled in a
CFBD startTimeTBD placeholder and never touched a "real" CFBD time.
"""

import pandas as pd

from bq_transform import _parse_fbschedules_time, _parse_fbschedules_date, _apply_fbschedules_schedule


def test_parses_standard_pm_time():
    assert _parse_fbschedules_time("6:00pm") == (18, 0)


def test_parses_standard_am_time():
    assert _parse_fbschedules_time("11:30am") == (11, 30)


def test_parses_noon_and_midnight_correctly():
    assert _parse_fbschedules_time("12:00pm") == (12, 0)
    assert _parse_fbschedules_time("12:00am") == (0, 0)


def test_unparseable_time_returns_none():
    assert _parse_fbschedules_time("TBD") is None
    assert _parse_fbschedules_time(None) is None
    assert _parse_fbschedules_time("") is None


def test_parses_weekday_month_day_date():
    assert _parse_fbschedules_date("Saturday, November 7", 2026).isoformat() == "2026-11-07"


def test_unparseable_date_returns_none():
    assert _parse_fbschedules_date("TBD", 2026) is None
    assert _parse_fbschedules_date(None, 2026) is None
    assert _parse_fbschedules_date("", 2026) is None


def test_overrides_midnight_eastern_placeholder():
    # 2026-08-27T04:00:00Z is CFBD's real TBD placeholder for the Ohio
    # Dominican @ Morehead State game -- 04:00 UTC - 4h (EDT) = 12:00 AM ET
    # on Aug 27, i.e. CFBD already has the right date, just no real time.
    df = pd.DataFrame([
        {"id": 401896383, "season": 2026, "startDate": pd.Timestamp("2026-08-27T04:00:00Z")},
    ])
    fb_times = {401896383: "6:00pm"}
    fb_dates = {401896383: "Thursday, August 27"}

    result = _apply_fbschedules_schedule(df, fb_dates, fb_times)

    # 6:00pm ET on Aug 27 = 22:00 UTC (EDT, +4h)
    assert result.loc[0, "startDate"] == pd.Timestamp("2026-08-27T22:00:00Z")


def test_overrides_a_real_cfbd_time_when_fbschedules_disagrees():
    # CFBD already has a real (non-midnight) time here -- fbschedules is now
    # the source of truth, so it should win anyway, not just fill in TBD gaps.
    df = pd.DataFrame([
        {"id": 1, "season": 2026, "startDate": pd.Timestamp("2026-09-05T23:30:00Z")},  # CFBD says 7:30pm ET
    ])
    fb_times = {1: "9:00pm"}
    fb_dates = {1: "Saturday, September 5"}

    result = _apply_fbschedules_schedule(df, fb_dates, fb_times)

    # 9:00pm ET on Sep 5 = 01:00 UTC on Sep 6 (EDT, +4h)
    assert result.loc[0, "startDate"] == pd.Timestamp("2026-09-06T01:00:00Z"), \
        "fbschedules' time should win over CFBD's even when CFBD's time looks real"


def test_uses_fbschedules_date_over_cfbd_date():
    # CFBD says Nov 7 1:00pm ET; fbschedules says the game is actually Nov 8
    # noon ET (e.g. a schedule change) -- both date and time should come from
    # fbschedules. Nov 7-8 2026 is also past the DST fallback, so this
    # exercises DST-aware conversion too.
    df = pd.DataFrame([
        {"id": 3, "season": 2026, "startDate": pd.Timestamp("2026-11-07T18:00:00Z")},
    ])
    fb_times = {3: "12:00pm"}
    fb_dates = {3: "Sunday, November 8"}

    result = _apply_fbschedules_schedule(df, fb_dates, fb_times)

    # 12:00pm ET on Nov 8 = 17:00 UTC (EST, +5h)
    assert result.loc[0, "startDate"] == pd.Timestamp("2026-11-08T17:00:00Z")


def test_falls_back_to_cfbd_date_when_fbschedules_date_unparseable():
    # A usable fbschedules time but no usable date -- keep CFBD's own
    # Eastern calendar date, just replace the time-of-day.
    df = pd.DataFrame([
        {"id": 4, "season": 2026, "startDate": pd.Timestamp("2026-09-12T23:00:00Z")},  # CFBD says 7:00pm ET Sep 12
    ])
    fb_times = {4: "7:30pm"}
    fb_dates = {4: "TBD"}

    result = _apply_fbschedules_schedule(df, fb_dates, fb_times)

    # 7:30pm ET on Sep 12 (CFBD's date) = 23:30 UTC (EDT, +4h)
    assert result.loc[0, "startDate"] == pd.Timestamp("2026-09-12T23:30:00Z")


def test_does_not_override_when_fbschedules_time_unparseable():
    df = pd.DataFrame([
        {"id": 1, "season": 2026, "startDate": pd.Timestamp("2026-09-05T23:30:00Z")},
    ])
    fb_times = {1: "TBD"}
    fb_dates = {1: "Saturday, September 5"}

    result = _apply_fbschedules_schedule(df, fb_dates, fb_times)

    assert result.loc[0, "startDate"] == pd.Timestamp("2026-09-05T23:30:00Z"), \
        "an unparseable fbschedules time means nothing usable to combine -- CFBD's startDate should be untouched"


def test_leaves_startdate_alone_when_fbschedules_has_no_data_at_all():
    df = pd.DataFrame([
        {"id": 2, "season": 2026, "startDate": pd.Timestamp("2026-08-27T04:00:00Z")},
    ])

    result = _apply_fbschedules_schedule(df, fb_dates={}, fb_times={})
    assert result.loc[0, "startDate"] == pd.Timestamp("2026-08-27T04:00:00Z")


if __name__ == "__main__":
    test_parses_standard_pm_time()
    test_parses_standard_am_time()
    test_parses_noon_and_midnight_correctly()
    test_unparseable_time_returns_none()
    test_parses_weekday_month_day_date()
    test_unparseable_date_returns_none()
    test_overrides_midnight_eastern_placeholder()
    test_overrides_a_real_cfbd_time_when_fbschedules_disagrees()
    test_uses_fbschedules_date_over_cfbd_date()
    test_falls_back_to_cfbd_date_when_fbschedules_date_unparseable()
    test_does_not_override_when_fbschedules_time_unparseable()
    test_leaves_startdate_alone_when_fbschedules_has_no_data_at_all()
    print("All fbschedules-schedule-override tests passed.")
