"""Determines which (season, week) slot(s) are "current" by querying the
same Mongo `games`/`games_test` collection this service patches, rather than
hardcoding a week number that would need manual updates every week.

Self-referential by design: the grid already knows its own current week from
the daily batch transform, so there's no need for this service to duplicate
that knowledge or guess at it from a calendar rule (season start date,
non-standard weeks, etc).
"""

from datetime import date, timedelta
from typing import List, Tuple

# Catches a Thu/Fri-night game whose Eastern calendar date has already
# rolled to "tomorrow" relative to when this runs, and a week whose games
# haven't kicked off yet today but will within this poll window.
LOOKBACK_DAYS = 1
LOOKAHEAD_DAYS = 2


def current_weeks(collection, today: date) -> List[Tuple[int, int]]:
    """Returns sorted, deduped (season, week) pairs for games whose stored
    `date` (a 'YYYY-MM-DD' string, per-timezone -- see
    transform/bq_transform.py's dateTimeStr formatting) falls within
    [today - LOOKBACK_DAYS, today + LOOKAHEAD_DAYS].

    `today` must be an America/New_York calendar date, not the container's
    UTC date (Cloud Run runs UTC) -- compute it in the caller via
    datetime.now(ZoneInfo("America/New_York")).date() and pass it in here,
    rather than defaulting to date.today() in this function.

    Filters to timezone='E' to pick one of the 6 timezone-fanout docs per
    game (transform/mongo_writer.py's doc model: one doc per
    (game_id, timezone), 6 timezone variants sharing game_id) -- otherwise
    the same (season, week) pair would just be repeated 6x for no benefit.
    Uses the existing (date, -1) index (transform/ensure_indexes.py).

    Normally returns exactly one pair. app.py loops over however many come
    back, so a rare multi-week boundary (e.g. a Tuesday with both a
    just-finished Monday-night game and a just-started midweek game) is
    handled without any special-casing here.
    """
    lo = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    hi = (today + timedelta(days=LOOKAHEAD_DAYS)).isoformat()
    cursor = collection.find(
        {"timezone": "E", "date": {"$gte": lo, "$lte": hi}},
        {"season": 1, "week": 1, "_id": 0},
    )
    return sorted({(doc["season"], doc["week"]) for doc in cursor})
