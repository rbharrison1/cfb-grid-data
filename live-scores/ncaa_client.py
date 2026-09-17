"""Fetches the NCAA scoreboard feed (github.com/henrygd/ncaa-api, public
demo instance) for a given (year, week).

week MUST be zero-padded to 2 digits ('02', not '2'). Verified by hand
before this service was written: an unpadded single-digit week doesn't 404
or error -- it silently falls back to returning whatever the CURRENT/live
scoreboard is, regardless of the year/week actually requested. Confirmed by
requesting weeks 0-5 unpadded and getting byte-identical "current" data back
every time, versus a zero-padded '02' correctly returning that specific
weekend's games. Getting this wrong means the caller silently patches the
wrong week's games with no visible error -- there's no status code or field
that flags the fallback.

The Mongo grid isn't FBS-only -- CFBD ingest pulls games without filtering
by classification (see bq_ingest.py's ENDPOINTS dict), so an FCS-vs-FCS game
with a mapped TV outlet can appear in the grid same as any FBS game.
fetch_all_divisions() hits both NCAA division scoreboards and merges them.
Verified by hand (2026 week 2): the "fbs" and "fcs" feeds overlap on any
game where either side is an FCS team (37 of 86 fbs-feed games also showed
up in the fcs feed, keyed on NCAA's own gameID) -- 45 games existed only in
the fcs feed. Dedup by gameID is required, not optional, or a shared game
would be matched and patched twice per poll cycle (harmless since the patch
is idempotent, just wasted work and inflated diagnostic counts).
"""

from typing import Dict, List, Optional, Tuple

import requests

NCAA_API_BASE = "https://ncaa-api.henrygd.me"
DIVISIONS = ("fbs", "fcs")


def fetch_scoreboard(year: int, week: int, division: str = "fbs", session: Optional[requests.Session] = None) -> dict:
    """GET /scoreboard/football/{division}/{year}/{week:02d}/all-conf and
    return the parsed JSON payload:
    {inputMD5Sum, instanceId, updated_at, games: [...]}.
    """
    url = f"{NCAA_API_BASE}/scoreboard/football/{division}/{year}/{week:02d}/all-conf"
    resp = (session or requests).get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def extract_games(payload: dict) -> List[dict]:
    """Flattens payload['games'] (each entry wrapped as {'game': {...}}) into
    a flat list of game dicts."""
    return [g["game"] for g in payload.get("games", []) if "game" in g]


def fetch_all_divisions(
    year: int, week: int, session: Optional[requests.Session] = None
) -> Tuple[List[dict], Dict[str, str]]:
    """Fetches both DIVISIONS and returns (deduped games, {division: error})
    -- one division failing doesn't drop the other's results. Dedup keeps
    whichever division's copy was seen first (fbs fetched before fcs); the
    two copies of a shared game are expected to be identical or
    near-identical (fetched moments apart), so which one wins doesn't
    matter for the live_* fields that get patched.
    """
    seen: Dict[str, dict] = {}
    errors: Dict[str, str] = {}
    for division in DIVISIONS:
        try:
            payload = fetch_scoreboard(year, week, division=division, session=session)
        except Exception as e:
            errors[division] = str(e)
            continue
        for game in extract_games(payload):
            seen.setdefault(game["gameID"], game)
    return list(seen.values()), errors
