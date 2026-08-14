"""Scrapes the college football TV schedule from fbschedules.com.

The site renders its schedule client-side via a JSON AJAX endpoint
(wp-admin/admin-ajax.php) rather than server-rendered HTML, so this hits that
endpoint directly instead of scraping the rendered page. The endpoint's
`schedule-week` param is an internal WordPress term id (e.g. "week-13697")
that isn't derivable from a week number alone and changes season to season,
so it's discovered from the page's week-select dropdown at request time
rather than hardcoded.
"""

import re
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

SCHEDULE_PAGE_URL = "https://fbschedules.com/college-football-tv-schedule/"
AJAX_URL = "https://fbschedules.com/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; cfb-grid-ingest/1.0; +https://cfb-grid.com)",
    "Accept": "application/json, text/html",
}

_WEEK_LABEL_RE = re.compile(r"Week\s+(\d+)", re.IGNORECASE)


def discover_week_ids(session: Optional[requests.Session] = None) -> Dict[int, str]:
    """Returns {week_number: schedule-week-id} by reading the page's week dropdown."""
    sess = session or requests
    resp = sess.get(SCHEDULE_PAGE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    select = soup.select_one("select.score-week-select")
    if select is None:
        raise ValueError("Could not find the week-select dropdown on the fbschedules.com schedule page")

    week_ids = {}
    for option in select.find_all("option"):
        value = option.get("value")
        label = option.get_text(strip=True)
        match = _WEEK_LABEL_RE.match(label)
        if value and match:
            week_ids[int(match.group(1))] = value
    return week_ids


def _parse_schedule_html(html: str, year: int, week: int) -> List[dict]:
    """Parses the desktop table markup out of the AJAX response's `html` field.

    The response embeds the same schedule twice (a `table.spring` desktop
    version and a `table.spring-alt` mobile version inside
    `div.mobile-alt-schedule`) -- only the desktop table is parsed to avoid
    double-counting games.
    """
    soup = BeautifulSoup(html, "html.parser")
    desktop = soup.select_one("div.desktop-alt-schedule")
    wrapper = desktop.select_one("div.current-season-week-scroe-wrapper") if desktop else None
    if wrapper is None:
        return []

    rows = []
    current_date = None
    for el in wrapper.find_all(["div", "table"], recursive=False):
        classes = el.get("class") or []
        if el.name == "div" and "bowl-year-bg" in classes:
            current_date = el.get_text(strip=True)
        elif el.name == "table" and "spring" in classes:
            rows.extend(_parse_table(el, current_date, year, week))
    return rows


def _parse_table(table, date: Optional[str], year: int, week: int) -> List[dict]:
    games = []
    for tr in table.select("tbody tr"):
        matchup_cell = tr.select_one('td[class*="spring1"]')
        if matchup_cell is None:
            continue
        # Ranked teams nest an inner span.school-name-content (the plain name)
        # inside an outer one (rank badge + name) -- keep only the leaf spans
        # so ranked and unranked matchups both yield exactly [away, home].
        all_spans = matchup_cell.select("span.school-name-content")
        schools = [s for s in all_spans if not s.select_one("span.school-name-content")]
        if len(schools) < 2:
            continue

        time_cell = tr.select_one("td.spring2")
        network_cell = tr.select_one("td.spring3")

        games.append({
            "season": year,
            "week": week,
            "date": date,
            "away_team": schools[0].get_text(strip=True),
            "home_team": schools[1].get_text(strip=True),
            "time": time_cell.get_text(strip=True) if time_cell else None,
            "network": network_cell.get_text(strip=True) if network_cell else None,
        })
    return games


def fetch_tv_schedule(
    year: int, week: int, session: Optional[requests.Session] = None, week_ids: Optional[Dict[int, str]] = None
) -> List[dict]:
    """Fetches and parses the TV schedule for a single CFBD week number."""
    sess = session or requests.Session()
    week_ids = week_ids if week_ids is not None else discover_week_ids(sess)
    week_id = week_ids.get(week)
    if week_id is None:
        raise ValueError(f"No fbschedules.com week id found for week {week} (available: {sorted(week_ids)})")

    params = {
        "action": "load_fbschedules_ajax",
        "type": "NCAA",
        "display": "current",
        "team": "",
        "current_season": year,
        "view": "weekly",
        "conference": "",
        "conference-division": "",
        "ncaa-subdivision": "",
        "ispreseason": "",
        "current-page-type": "",
        "is_spring_week_only": "",
        "pid": "92742",
        "schedule-week": week_id,
        "is_playoff": "false",
    }
    resp = sess.get(AJAX_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    return _parse_schedule_html(payload.get("html", ""), year, week)


def fetch_full_season(year: int, session: Optional[requests.Session] = None) -> List[dict]:
    """Fetches and parses every week of the season in one call each."""
    sess = session or requests.Session()
    week_ids = discover_week_ids(sess)

    rows = []
    for week in sorted(week_ids):
        rows.extend(fetch_tv_schedule(year, week, session=sess, week_ids=week_ids))
    return rows
