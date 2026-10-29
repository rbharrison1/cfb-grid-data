"""MongoDB writes for the college basketball grid.

Same delete-then-insert + frozen-completed semantics as the football writer
(transform/mongo_writer.py, see cfb-grid-data's CLAUDE.md), but the slot is
(season, slate_date) -- all six timezone copies of a date at once -- instead
of (season, week, timezone). Every date in the requested range is cleared,
including dates with no games in this run, so a game that moves dates or
loses its broadcast doesn't linger as a stale document.
"""

from datetime import date, timedelta
from typing import Any, Dict, List

import certifi
from pymongo import MongoClient


def _dates(start: date, end: date):
    d = start
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def write_date_range(uri: str, db_name: str, collection_name: str, season: int,
                     start: date, end: date, docs_by_date: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, int]]:
    """Rewrites every slate date in [start, end]. A game already stored as
    completed is frozen: never deleted, never reinserted. Returns per-date
    {written, deleted, frozen} counts for dates that had any activity."""
    client = MongoClient(
        uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=60000,
        connectTimeoutMS=60000,
        socketTimeoutMS=60000,
    )
    results = {}
    try:
        collection = client[db_name][collection_name]
        for slate_date in _dates(start, end):
            slot_filter = {'season': season, 'slate_date': slate_date}
            frozen_ids = set(collection.distinct('game_id', {**slot_filter, 'completed': True}))
            deleted = collection.delete_many({**slot_filter, 'completed': {'$ne': True}}).deleted_count
            docs = [d for d in docs_by_date.get(slate_date, []) if d['game_id'] not in frozen_ids]
            if docs:
                collection.insert_many(docs)
            if docs or deleted or frozen_ids:
                results[slate_date] = {'written': len(docs), 'deleted': deleted, 'frozen_games': len(frozen_ids)}
        return results
    finally:
        client.close()


def ensure_indexes(uri: str, db_name: str = 'cfb-grid', collection_name: str = 'cbb-games'):
    """Indexes shaped to cfb-grid-server's routes/cbbGame.mjs queries
    (find by date / outlet, sorted by date then start; distinct date) and to
    this writer's own (season, slate_date) slot filter. Idempotent."""
    client = MongoClient(uri, tlsCAFile=certifi.where())
    try:
        collection = client[db_name][collection_name]
        collection.create_index([('date', 1), ('start', 1)])
        collection.create_index([('outlet', 1), ('date', 1), ('start', 1)])
        collection.create_index([('season', 1), ('slate_date', 1), ('completed', 1)])
    finally:
        client.close()
