#!/usr/bin/env python3
"""Smoke tests for the fbschedules.com HTML parser, run against a fixture
that mirrors the real AJAX response structure (verified against a live
capture during development): a desktop table (parsed) plus a duplicate
mobile table (must be ignored), an unranked matchup, and a ranked-vs-ranked
matchup (whose nested rank/name spans must not get concatenated together).
"""

from fbschedules import _parse_schedule_html

FIXTURE_HTML = """
<div class="desktop-alt-schedule">
    <div class="current-season-week-scroe-wrapper week-13697 ">
        <div class="bowl-bg"> Schedule - Week 1</div>
        <div class="bowl-year-bg">Thursday, September 3</div>
        <table class="spring">
            <thead><tr><th>Matchup</th><th>Time</th><th>TV</th><th>Tickets</th></tr></thead>
            <tbody>
                <tr>
                    <td class="spring1 226550">
                        <div class="row-schedule-content"><span class="school-name-content">Kentucky Christian</span> <span> at </span><span class="school-name-content">Morehead State</span></div>
                    </td>
                    <td class="spring2">6:00pm</td>
                    <td class="spring3">ESPN+</td>
                    <td class="spring4"><a href="#">Buy Tickets</a></td>
                </tr>
            </tbody>
        </table>
        <div class="bowl-year-bg">Sunday, September 6</div>
        <table class="spring">
            <tbody>
                <tr>
                    <td class="spring1 226679">
                        <div class="row-schedule-content"><span class="school-name-content"><span class="team-rank">21</span> <span class="school-name-content">Louisville</span></span> <span> vs </span> <span class="school-name-content"><span class="team-rank">9</span> <span class="school-name-content">Ole Miss</span></span></div>
                    </td>
                    <td class="spring2">7:30pm</td>
                    <td class="spring3">ABC</td>
                    <td class="spring4"><a href="#">Buy Tickets</a></td>
                </tr>
            </tbody>
        </table>
    </div>
</div>
<div class="mobile-alt-schedule">
    <div class="current-season-week-scroe-wrapper week-13697 hidden">
        <div class="bowl-bg"> Schedule - Week 1</div>
        <div class="bowl-year-bg">Thursday, September 3</div>
        <table class="spring-alt">
            <tbody>
                <tr>
                    <td class="spring1 226550">
                        <div class="spring-row">Kentucky Christian</div>
                        <div class="spring-row">Morehead State</div>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
</div>
"""


def test_parses_desktop_rows_only():
    rows = _parse_schedule_html(FIXTURE_HTML, 2026, 1)
    assert len(rows) == 2, f"expected 2 rows (mobile duplicate must be ignored), got {len(rows)}: {rows}"


def test_unranked_matchup_fields():
    rows = _parse_schedule_html(FIXTURE_HTML, 2026, 1)
    game = rows[0]
    assert game["away_team"] == "Kentucky Christian"
    assert game["home_team"] == "Morehead State"
    assert game["date"] == "Thursday, September 3"
    assert game["time"] == "6:00pm"
    assert game["network"] == "ESPN+"
    assert game["season"] == 2026
    assert game["week"] == 1


def test_ranked_matchup_strips_rank_prefix():
    rows = _parse_schedule_html(FIXTURE_HTML, 2026, 1)
    game = rows[1]
    assert game["away_team"] == "Louisville", f"rank badge leaked into name: {game['away_team']!r}"
    assert game["home_team"] == "Ole Miss", f"rank badge leaked into name: {game['home_team']!r}"
    assert game["date"] == "Sunday, September 6"
    assert game["network"] == "ABC"


if __name__ == "__main__":
    test_parses_desktop_rows_only()
    test_unranked_matchup_fields()
    test_ranked_matchup_strips_rank_prefix()
    print("All fbschedules parser tests passed.")
