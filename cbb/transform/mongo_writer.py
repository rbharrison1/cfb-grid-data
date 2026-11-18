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
from pymongo import MongoClient, UpdateOne


def _dates(start: date, end: date):
    d = start
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


# Fields a frozen (completed) game still gets refreshed every run: game
# context for cfb-grid-web's GameModal, never scores, records or status.
# Without this, games that went final before these fields existed would
# never get them, and a final game's schedule window would stop updating.
MODAL_REFRESH_FIELDS = (
    'away_line_scores', 'home_line_scores', 'venue_name', 'city', 'state',
    'conference_game', 'attendance', 'excitement', 'notes',
    'away_team_schedule', 'home_team_schedule',
)


def frozen_refresh_ops(docs: List[Dict[str, Any]], frozen_ids, slot_filter: Dict[str, Any]) -> List[UpdateOne]:
    """One $set of MODAL_REFRESH_FIELDS per frozen game per timezone copy."""
    ops = []
    for doc in docs:
        if doc['game_id'] in frozen_ids:
            fields = {f: doc[f] for f in MODAL_REFRESH_FIELDS if f in doc}
            if fields:
                ops.append(UpdateOne({**slot_filter, 'game_id': doc['game_id'], 'timezone': doc['timezone']},
                                     {'$set': fields}))
    return ops


def write_date_range(uri: str, db_name: str, collection_name: str, season: int,
                     start: date, end: date, docs_by_date: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, int]]:
    """Rewrites every slate date in [start, end]. A game already stored as
    completed is frozen: never deleted, never reinserted. Returns per-date
    {written, deleted, frozen_games, frozen_refreshed} counts for dates that had any activity."""
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
            date_docs = docs_by_date.get(slate_date, [])
            refresh = frozen_refresh_ops(date_docs, frozen_ids, slot_filter)
            if refresh:
                collection.bulk_write(refresh, ordered=False)
            docs = [d for d in date_docs if d['game_id'] not in frozen_ids]
            if docs:
                collection.insert_many(docs)
            if docs or deleted or frozen_ids:
                results[slate_date] = {'written': len(docs), 'deleted': deleted, 'frozen_games': len(frozen_ids),
                                       'frozen_refreshed': len(refresh)}
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
        # The frozen-game refresh updates one doc per game per timezone.
        collection.create_index([('game_id', 1), ('timezone', 1)])
    finally:
        client.close()
