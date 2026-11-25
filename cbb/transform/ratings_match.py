"""Matches T-Rank / NET team names to CBBD team ids, and looks up each team's
ratings as of a game's date.

Neither source shares an id with CBBD, so matching is by name, in tiers:
an explicit alias (config/ratings_aliases.py), then CBBD `school`, then
`shortDisplayName`, then `abbreviation`, each compared after normalize().
A tier only counts if it points at exactly one team, so an ambiguous name is
reported as unmatched rather than silently attached to the wrong school.

Snapshots are the daily ingest runs (raw_torvik_ratings / raw_net_rankings,
one batch per `_ingested_at`). A game gets the latest snapshot dated on or
before its Eastern slate date. The daily ingest runs at 5:30 AM ET, before any
tip-off, so that is the team's rating going into the game; completed games are
then frozen by mongo_writer, and re-transforms keep picking the same snapshot
because the choice is keyed on the game's date, not on "latest".
"""

import re
import unicodedata
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.ratings_aliases import NET_ALIASES, TORVIK_ALIASES


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join("st" if t in ("state", "saint") else t for t in s.split())


class TeamMatcher:
    def __init__(self, cbbd_teams: List[dict], aliases: Dict[str, str]):
        self.aliases = aliases
        self.school_ids = {t["school"]: int(t["id"]) for t in cbbd_teams}
        self.tiers: List[Dict[str, set]] = []
        for field in ("school", "shortDisplayName", "abbreviation"):
            tier = defaultdict(set)
            for t in cbbd_teams:
                if t.get(field):
                    tier[normalize(t[field])].add(int(t["id"]))
            self.tiers.append(tier)
        self.unmatched = Counter()

    def match(self, name: str) -> Optional[int]:
        if name in self.aliases:
            team_id = self.school_ids.get(self.aliases[name])
            if team_id is not None:
                return team_id
        key = normalize(name)
        for tier in self.tiers:
            ids = tier.get(key)
            if ids and len(ids) == 1:
                return next(iter(ids))
        self.unmatched[name] += 1
        return None


def torvik_matcher(cbbd_teams):
    return TeamMatcher(cbbd_teams, TORVIK_ALIASES)


def net_matcher(cbbd_teams):
    return TeamMatcher(cbbd_teams, NET_ALIASES)


def _num(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


class _Snapshots:
    """team_id -> rating dict, per snapshot date, with an on-or-before lookup."""

    def __init__(self, by_date: Dict[date, Dict[int, dict]]):
        self.dates = sorted(by_date)
        self.by_date = by_date

    def on(self, on: date) -> Tuple[Optional[date], Dict[int, dict]]:
        i = bisect_right(self.dates, on)
        if not i:
            return None, {}
        d = self.dates[i - 1]
        return d, self.by_date[d]


def _group(rows: Iterable[dict], name_field: str, matcher: TeamMatcher, build) -> _Snapshots:
    by_date: Dict[date, Dict[int, dict]] = defaultdict(dict)
    for row in rows:
        team_id = matcher.match(row.get(name_field) or "")
        if team_id is None:
            continue
        by_date[date.fromisoformat(row["snapshot_date"])][team_id] = build(row)
    return _Snapshots(by_date)


def _torvik_stats(row: dict) -> dict:
    adj_oe, adj_de = _num(row.get("adj_oe")), _num(row.get("adj_de"))
    return {
        "torvik_rank": row.get("torvik_rank"),
        "torvik_adj_em": _round(adj_oe - adj_de, 2) if adj_oe is not None and adj_de is not None else None,
        "torvik_adj_oe": _round(adj_oe, 2),
        "torvik_adj_oe_rank": row.get("adj_oe_rank"),
        "torvik_adj_de": _round(adj_de, 2),
        "torvik_adj_de_rank": row.get("adj_de_rank"),
        "torvik_adj_t": _round(_num(row.get("adj_t")), 1),
        "torvik_adj_t_rank": row.get("adj_t_rank"),
        "torvik_barthag": _round(_num(row.get("barthag")), 4),
    }


def _net_stats(row: dict) -> dict:
    quads = {q: row.get(q) for q in ("q1", "q2", "q3", "q4") if row.get(q)}
    out = {"net_rank": row.get("net_rank"), "_net_through": row.get("through_date")}
    if quads:
        out["quad_records"] = quads
    return out


class RatingsLookup:
    """`<side>_stats` and `ratings_as_of` for a team on a slate date."""

    def __init__(self, torvik_rows: List[dict], net_rows: List[dict], cbbd_teams: List[dict]):
        self.torvik_match = torvik_matcher(cbbd_teams)
        self.net_match = net_matcher(cbbd_teams)
        self.torvik = _group(torvik_rows, "team", self.torvik_match, _torvik_stats)
        self.net = _group(net_rows, "school", self.net_match, _net_stats)

    def stats_for(self, team_id, slate_date: date) -> Optional[Dict[str, Any]]:
        if team_id is None:
            return None
        _, torvik = self.torvik.on(slate_date)
        _, net = self.net.on(slate_date)
        merged = {**torvik.get(int(team_id), {}), **net.get(int(team_id), {})}
        merged.pop("_net_through", None)
        merged = {k: v for k, v in merged.items() if v is not None}
        return merged or None

    def as_of(self, slate_date: date) -> Optional[Dict[str, str]]:
        """Dates the shown ratings are from: the Torvik snapshot's date, and
        the NET's own 'through games' date."""
        torvik_date, _ = self.torvik.on(slate_date)
        _, net = self.net.on(slate_date)
        net_through = next((s["_net_through"] for s in net.values() if s.get("_net_through")), None)
        out = {}
        if torvik_date:
            out["torvik"] = torvik_date.isoformat()
        if net_through:
            out["net"] = net_through
        return out or None

    def unmatched(self) -> Dict[str, Dict[str, int]]:
        # Each name is counted once per snapshot it appeared in.
        return {"torvik": dict(self.torvik_match.unmatched), "net": dict(self.net_match.unmatched)}
