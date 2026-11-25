#!/usr/bin/env python3
"""T-Rank / NET team matching and per-game snapshot selection.

Matching runs against real data saved 2026-11-24 in cbb/test_fixtures/: the
2026-27 CBBD team list, the 2026-27 Torvik CSV, and the 2025-26 final NET
(this season's NET wasn't out yet).
"""

import json
import os
import sys

import pytest

from cbb_transform import build_documents
from config.ratings_aliases import NET_ALIASES, TORVIK_ALIASES
from ratings_match import RatingsLookup, net_matcher, normalize, torvik_matcher

HERE = os.path.dirname(__file__)
FIXTURES = os.path.join(HERE, "..", "test_fixtures")
sys.path.append(os.path.join(HERE, "..", "ingest"))  # appended, so it can't shadow this dir's app.py
import ratings_ingest  # noqa: E402

# Left D1 after 2025-26, so it's in the saved NET but not in CBBD's 2027 list.
NET_NOT_D1_IN_2027 = {"Saint Francis"}


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def teams():
    return json.loads(_read("cbbd_teams_2027.json"))


def test_normalize():
    assert normalize("San José State") == normalize("San Jose St.")
    assert normalize("Hawai'i") == normalize("Hawaii")
    assert normalize("Saint Joseph's") == normalize("St. Joseph's")
    assert normalize("Texas A&M") == "texas a and m"


def test_alias_targets_are_real_cbbd_schools(teams):
    schools = {t["school"] for t in teams}
    assert set(TORVIK_ALIASES.values()) - schools == set()
    assert set(NET_ALIASES.values()) - schools == set()


def test_every_torvik_team_matches_a_distinct_cbbd_team(teams):
    matcher = torvik_matcher(teams)
    rows = ratings_ingest.parse_torvik_csv(_read("torvik_2027_team_results.csv"))
    ids = {r["team"]: matcher.match(r["team"]) for r in rows}
    assert dict(matcher.unmatched) == {}
    assert len(set(ids.values())) == len(rows) == len(teams)


def test_every_net_team_matches_a_distinct_cbbd_team(teams):
    matcher = net_matcher(teams)
    rows = ratings_ingest.parse_net_html(_read("net_rankings.html"))
    ids = {r["school"]: matcher.match(r["school"]) for r in rows}
    assert set(matcher.unmatched) == NET_NOT_D1_IN_2027
    matched = [i for i in ids.values() if i is not None]
    assert len(set(matched)) == len(matched)


def test_both_sources_agree_on_spot_checks(teams):
    by_school = {t["school"]: int(t["id"]) for t in teams}
    tv, net = torvik_matcher(teams), net_matcher(teams)
    for torvik_name, net_name, school in [
        ("Connecticut", "UConn", "UConn"),
        ("Miami FL", "Miami (FL)", "Miami"),
        ("Miami OH", "Miami (OH)", "Miami (OH)"),
        ("Saint Mary's", "Saint Mary's (CA)", "Saint Mary's"),
        ("Loyola Marymount", "LMU (CA)", "Loyola Marymount"),
        ("Southern", "Southern U.", "Southern"),
        ("USC", "Southern California", "USC"),
    ]:
        assert tv.match(torvik_name) == by_school[school], torvik_name
        assert net.match(net_name) == by_school[school], net_name


TEAMS = [
    {"id": 1, "sourceId": "150", "school": "Duke", "shortDisplayName": "Duke", "abbreviation": "DUKE"},
    {"id": 2, "sourceId": "153", "school": "North Carolina", "shortDisplayName": "UNC", "abbreviation": "UNC"},
]


def _torvik(day, team, rank, oe=110.0, de=95.0):
    return {"snapshot_date": f"2026-11-{day:02d}", "team": team, "torvik_rank": rank,
            "adj_oe": oe, "adj_de": de, "barthag": 0.9,
            "adj_oe_rank": 11, "adj_de_rank": 22, "adj_t": 68.04, "adj_t_rank": 150}


def _net(day, school, rank):
    return {"snapshot_date": f"2026-12-{day:02d}", "through_date": f"2026-12-{day - 1:02d}",
            "school": school, "net_rank": rank, "q1": "2-1", "q2": "1-0", "q3": "0-0", "q4": "3-0"}


def test_lookup_uses_the_latest_snapshot_on_or_before_the_game_date():
    from datetime import date
    lookup = RatingsLookup([_torvik(20, "Duke", 5), _torvik(23, "Duke", 3)], [], TEAMS)
    assert lookup.stats_for(1, date(2026, 11, 19)) is None          # before the first snapshot
    assert lookup.stats_for(1, date(2026, 11, 20))["torvik_rank"] == 5
    assert lookup.stats_for(1, date(2026, 11, 22))["torvik_rank"] == 5  # frozen, not "latest"
    assert lookup.stats_for(1, date(2026, 11, 25))["torvik_rank"] == 3
    assert lookup.stats_for(2, date(2026, 11, 25)) is None          # team not in the snapshot
    assert lookup.as_of(date(2026, 11, 22)) == {"torvik": "2026-11-20"}


def test_lookup_merges_torvik_and_net():
    from datetime import date
    lookup = RatingsLookup([_torvik(30, "Duke", 4, oe=120.4, de=92.15)], [_net(2, "Duke", 7)], TEAMS)
    assert lookup.stats_for(1, date(2026, 12, 3)) == {
        "torvik_rank": 4, "torvik_adj_em": 28.25,
        "torvik_adj_oe": 120.4, "torvik_adj_oe_rank": 11, "torvik_adj_de": 92.15, "torvik_adj_de_rank": 22,
        "torvik_adj_t": 68.0, "torvik_adj_t_rank": 150,
        "torvik_barthag": 0.9, "net_rank": 7,
        "quad_records": {"q1": "2-1", "q2": "1-0", "q3": "0-0", "q4": "3-0"},
    }
    assert lookup.as_of(date(2026, 12, 3)) == {"torvik": "2026-11-30", "net": "2026-12-01"}


def test_documents_carry_side_stats():
    game = {
        "id": 500, "season": 2027, "seasonType": "regular", "status": "scheduled",
        "startDate": "2026-11-24T23:00:00.000Z", "startTimeTbd": False,
        "awayTeamId": 2, "awayTeam": "North Carolina", "homeTeamId": 1, "homeTeam": "Duke",
        "awayPoints": 0, "homePoints": 0, "neutralSite": False,
    }
    docs, stats = build_documents(
        [game], {}, TEAMS, [], [game],
        torvik_rows=[_torvik(24, "Duke", 2), _torvik(24, "UNC", 9), _torvik(24, "Nowhere St.", 300)],
    )
    doc = docs["2026-11-24"][0]
    assert doc["home_stats"]["torvik_rank"] == 2 and doc["away_stats"]["torvik_rank"] == 9
    assert doc["ratings_as_of"] == {"torvik": "2026-11-24"}
    assert stats["ratings_unmatched"] == {"torvik": {"Nowhere St.": 1}, "net": {}}
