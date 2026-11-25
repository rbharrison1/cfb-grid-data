#!/usr/bin/env python3
"""T-Rank CSV and NET page parsing, against real copies saved 2026-11-24
(cbb/test_fixtures/), plus the fetch guards."""

import os

import pytest

import ratings_ingest

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "test_fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_torvik_csv_parses_every_team_by_position():
    rows = ratings_ingest.parse_torvik_csv(_read("torvik_2027_team_results.csv"))
    assert len(rows) == 365
    top = rows[0]
    assert top["torvik_rank"] == 1 and top["team"] == "Houston" and top["conf"] == "B12"
    assert top["adj_oe"] == pytest.approx(124.655, abs=1e-3)
    assert top["adj_de"] == pytest.approx(93.868, abs=1e-3)
    assert 0 < top["barthag"] < 1
    assert top["adj_oe_rank"] == 3 and top["adj_de_rank"] == 3
    assert sorted(r["torvik_rank"] for r in rows) == list(range(1, 366))


def test_torvik_tempo_rank_is_derived_fastest_first():
    rows = ratings_ingest.parse_torvik_csv(_read("torvik_2027_team_results.csv"))
    by_rank = sorted(rows, key=lambda r: r["adj_t_rank"])
    assert [r["adj_t_rank"] for r in by_rank] == list(range(1, 366))  # no ties in this snapshot
    assert all(a["adj_t"] >= b["adj_t"] for a, b in zip(by_rank, by_rank[1:]))
    # Off/def ranks come straight from the CSV and agree with the values.
    assert min(rows, key=lambda r: -r["adj_oe"])["adj_oe_rank"] == 1
    assert min(rows, key=lambda r: r["adj_de"])["adj_de_rank"] == 1


def test_torvik_tempo_ties_share_the_better_rank():
    header = "rank,team,conf,record,adjoe,oe Rank,adjde,de Rank,barthag,rank,WAB,adjt"
    body = "\n".join([
        "1,A,X,1-0,110,1,90,1,0.9,1,1.0,70.0",
        "2,B,X,1-0,105,2,95,2,0.8,2,0.5,70.0",
        "3,C,X,0-1,100,3,99,3,0.5,3,0.0,65.0",
    ])
    rows = ratings_ingest.parse_torvik_csv(header + "\n" + body)
    assert [r["adj_t_rank"] for r in rows] == [1, 1, 3]


def test_torvik_rejects_a_non_csv_body():
    with pytest.raises(ValueError):
        ratings_ingest.parse_torvik_csv("<html><body>Verifying your browser...</body></html>")


def test_net_page_parses_table_and_through_date():
    rows = ratings_ingest.parse_net_html(_read("net_rankings.html"))
    assert len(rows) == 365
    top = rows[0]
    assert top == {
        "through_date": "2026-04-06", "net_rank": 1, "school": "Michigan", "record": "37-3",
        "conf": "Big Ten", "road": "11-0", "neutral": "12-2", "home": "14-1", "prev": 2,
        "q1": "21-3", "q2": "7-0", "q3": "6-0", "q4": "3-0",
    }


@pytest.mark.parametrize("text,expected", [
    ("Through Games Apr. 06 2026", "2026-04-06"),
    ("Through Games Dec. 1 2026", "2026-12-01"),
    ("Through Games Sept. 30, 2026", "2026-09-30"),
    ("Through Games March 15 2027", "2027-03-15"),
    ("no date here", None),
])
def test_net_through_date(text, expected):
    assert ratings_ingest.parse_net_through_date(text) == expected


class _Resp:
    def __init__(self, status=200, content_type="text/csv", text="", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        return self.responses.pop(0)


def test_fetch_torvik_refuses_a_bot_check_page():
    session = _Session([_Resp(content_type="text/html", text="<html>challenge</html>")])
    with pytest.raises(ValueError, match="not CSV"):
        ratings_ingest.fetch_torvik(2027, session)
    assert session.calls == 1


def test_retries_once_on_429_honoring_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(ratings_ingest.time, "sleep", waits.append)
    session = _Session([_Resp(status=429, headers={"Retry-After": "3"}), _Resp(status=429)])
    with pytest.raises(RuntimeError):
        ratings_ingest.fetch_torvik(2027, session)
    assert session.calls == 2
    assert waits == [ratings_ingest.MIN_RETRY_WAIT_S]  # never faster than the 10s crawl delay
