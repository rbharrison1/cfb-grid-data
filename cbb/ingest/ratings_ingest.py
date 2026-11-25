"""Daily team-ratings snapshots: Bart Torvik's T-Rank and the NCAA NET.

Neither is part of the CBBD API. Each is one public page fetched once per
daily run, politely:
  - one request per host per run, with a User-Agent that names the site;
  - one retry on 429/5xx, honoring Retry-After (at least the 10s Crawl-Delay
    barttorvik.com's robots.txt asks for);
  - a response that isn't the expected format (e.g. a bot-check page) is a
    failure, never something to work around.

Both URLs are allowed by their site's robots.txt as of 2026-11-24 (Torvik
disallows *.json and a few .php pages, not the season CSV). Rows are returned
raw-ish (source names, no team matching): the transform matches them to CBBD
team ids, so a matching fix never needs a re-fetch.
"""

import csv
import io
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional

import requests

USER_AGENT = "cfbgrid.com ratings ingest (+https://cfbgrid.com)"
TIMEOUT_S = 30
MIN_RETRY_WAIT_S = 10
MAX_RETRY_WAIT_S = 120

TORVIK_URL = "https://barttorvik.com/{season}_team_results.csv"
NET_URL = "https://www.ncaa.com/rankings/basketball-men/d1/ncaa-mens-basketball-net-rankings"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _get(session: requests.Session, url: str) -> requests.Response:
    """GET with a single polite retry on 429/5xx."""
    resp = session.get(url, timeout=TIMEOUT_S)
    if resp.status_code == 429 or resp.status_code >= 500:
        try:
            wait = int(resp.headers.get("Retry-After", MIN_RETRY_WAIT_S))
        except ValueError:
            wait = MIN_RETRY_WAIT_S
        time.sleep(min(max(wait, MIN_RETRY_WAIT_S), MAX_RETRY_WAIT_S))
        resp = session.get(url, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp


def _num(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: str) -> Optional[int]:
    n = _num(value)
    return int(n) if n is not None else None


# --- Torvik ------------------------------------------------------------------

# Header name -> output field. The header has two "rank" columns (T-Rank first,
# then the Barthag rank), so T-Rank is read by position (column 0) instead.
_TORVIK_FIELDS = {
    "team": ("team", str),
    "conf": ("conf", str),
    "record": ("record", str),
    "adjoe": ("adj_oe", _num),
    "oe Rank": ("adj_oe_rank", _int),
    "adjde": ("adj_de", _num),
    "de Rank": ("adj_de_rank", _int),
    "barthag": ("barthag", _num),
    "WAB": ("wab", _num),
    "adjt": ("adj_t", _num),
}


def parse_torvik_csv(text: str) -> List[dict]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or header[0].strip().lower() != "rank" or "team" not in header:
        raise ValueError(f"unexpected Torvik CSV header: {header[:5] if header else header}")
    index = {}
    for i, name in enumerate(header):
        index.setdefault(name.strip(), i)
    missing = [name for name in _TORVIK_FIELDS if name not in index]
    if missing:
        raise ValueError(f"Torvik CSV missing columns: {missing}")

    rows = []
    for cells in reader:
        if len(cells) < len(header) or not cells[1].strip():
            continue
        row = {"torvik_rank": _int(cells[0])}
        for name, (field, conv) in _TORVIK_FIELDS.items():
            raw = cells[index[name]].strip()
            row[field] = conv(raw) if conv is not str else raw
        rows.append(row)

    # The CSV has offense/defense ranks but no tempo rank, so it's derived
    # here from the same snapshot: fastest adjusted tempo is #1, as on
    # barttorvik.com. Ties share the better rank.
    tempos = sorted((r["adj_t"] for r in rows if r["adj_t"] is not None), reverse=True)
    first_index = {}
    for i, t in enumerate(tempos):
        first_index.setdefault(t, i + 1)
    for r in rows:
        r["adj_t_rank"] = first_index.get(r["adj_t"])
    return rows


def fetch_torvik(season: int, session: requests.Session = None) -> List[dict]:
    resp = _get(session or _session(), TORVIK_URL.format(season=season))
    content_type = resp.headers.get("Content-Type", "")
    if "csv" not in content_type:
        raise ValueError(f"Torvik returned {content_type or 'no content type'}, not CSV (bot check?)")
    return parse_torvik_csv(resp.text)


# --- NET ---------------------------------------------------------------------

class _FirstTableParser(HTMLParser):
    """Collects the cell text of the first <table> on the page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._depth = 0
        self._done = False
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if self._done or not self._depth:
            return
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._depth -= 1
            if not self._depth:
                self._done = True

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


_NET_COLUMNS = {
    "Rank": ("net_rank", _int),
    "School": ("school", str),
    "Record": ("record", str),
    "Conf": ("conf", str),
    "Road": ("road", str),
    "Neutral": ("neutral", str),
    "Home": ("home", str),
    "Prev": ("prev", _int),
    "Quad 1": ("q1", str),
    "Quad 2": ("q2", str),
    "Quad 3": ("q3", str),
    "Quad 4": ("q4", str),
}

_THROUGH_RE = re.compile(r"Through Games\s+([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")


def parse_net_through_date(html_text: str) -> Optional[str]:
    """ISO date from the page's 'Through Games Apr. 06 2026' line, or None."""
    m = _THROUGH_RE.search(html_text)
    if not m:
        return None
    month, day, year = m.groups()
    try:
        # First three letters cover "Apr.", "Sept." and "March" alike.
        return datetime.strptime(f"{month[:3]} {day} {year}", "%b %d %Y").date().isoformat()
    except ValueError:
        return None


def parse_net_html(html_text: str) -> List[dict]:
    parser = _FirstTableParser()
    parser.feed(html_text)
    if not parser.rows:
        raise ValueError("NET page has no table")
    header = parser.rows[0]
    missing = [name for name in _NET_COLUMNS if name not in header]
    if missing:
        raise ValueError(f"NET table missing columns: {missing}")
    through_date = parse_net_through_date(html_text)
    if not through_date:
        raise ValueError("NET page has no 'Through Games' date")

    rows = []
    for cells in parser.rows[1:]:
        if len(cells) != len(header):
            continue
        row = {"through_date": through_date}
        for name, (field, conv) in _NET_COLUMNS.items():
            raw = cells[header.index(name)]
            row[field] = conv(raw) if conv is not str else raw
        if row["net_rank"] is not None and row["school"]:
            rows.append(row)
    return rows


def fetch_net(session: requests.Session = None) -> List[dict]:
    resp = _get(session or _session(), NET_URL)
    return parse_net_html(resp.text)
