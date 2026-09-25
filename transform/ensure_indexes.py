"""One-time index creation for the 'games' collection.

Ported from cfb-grid-python/mern/python/v2/crud_gui.py::create_indexes.
create_index is idempotent (a no-op if the same key spec already exists), so
this is safe to run more than once, but it's a manual/one-off step -- not
called on every /transform request.

Usage: python ensure_indexes.py (reads MONGODB_URI from the environment)
"""

import os
from pymongo import MongoClient
import certifi


def ensure_indexes(uri: str, db_name: str = 'cfb-grid', collection_name: str = 'games'):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    try:
        collection = client[db_name][collection_name]
        collection.create_index([("season", 1), ("week", 1)])
        collection.create_index([("timezone", 1)])
        collection.create_index([("away_team", 1)])
        collection.create_index([("home_team", 1)])
        collection.create_index([("date", -1)])
        collection.create_index([("completed", 1)])
        collection.create_index([("season", 1), ("week", 1), ("away_team", 1), ("home_team", 1)])
        # cfb-grid-server's routes/game.mjs queries this collection with
        # .find({date/outlet}).sort({date: 1, print_time: 1}) on every one of
        # its GET routes (/, /:outlet, /date/:date). None of the indexes
        # above cover print_time or the compound (date, print_time) sort key
        # at all, so as the season's document count grew, that sort forced
        # either a full collection scan or an expensive in-memory sort on
        # every request -- confirmed live on 2026-09-19: /game/date/:date
        # was taking 16-30s (one request hit Heroku's 30s router timeout and
        # 503'd) while /game/dates (a $group aggregation untouched by this)
        # stayed under 200ms. These two indexes are shaped to exactly match
        # cfb-grid-server's actual filter+sort so Mongo can satisfy both
        # from the index alone.
        collection.create_index([("date", 1), ("print_time", 1)])
        collection.create_index([("outlet", 1), ("date", 1), ("print_time", 1)])
    finally:
        client.close()


if __name__ == '__main__':
    mongodb_uri = os.environ["MONGODB_URI"]
    ensure_indexes(mongodb_uri)
    print("Indexes ensured on 'games' collection.")
