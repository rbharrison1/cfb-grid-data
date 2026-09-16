"""Database utilities for MongoDB operations.

Ported unchanged from cfb-grid-python/mern/python/v2/utils/db.py.
"""

from pymongo import MongoClient
import certifi
from typing import List, Dict, Any

def write_to_mongodb(uri: str, db_name: str, collection_name: str,
                    data: List[Dict[str, Any]], year: int, week: int, timezone: str,
                    overwrite: bool = True) -> List[Dict[str, Any]]:
    """
    Write data to MongoDB collection.

    Args:
        uri (str): MongoDB connection URI
        db_name (str): Database name
        collection_name (str): Collection name
        data (List[Dict]): Data to write
        year (int): Season year
        week (int): Week number
        timezone (str): Timezone code
        overwrite (bool): If True, delete existing non-completed data for this
            slot first (games already stored as completed are frozen and
            left untouched -- see below). If False, append to existing data.

    Returns:
        List[Dict[str, Any]]: Copy of the inserted data with MongoDB IDs removed

    Raises:
        pymongo.errors.PyMongoError: If database operation fails
    """
    client = MongoClient(
        uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=60000,  # 60 second timeout for server selection
        connectTimeoutMS=60000,          # 60 second timeout for initial connection
        socketTimeoutMS=60000            # 60 second timeout for socket operations
    )
    try:
        # Test the connection
        client.server_info()

        db = client[db_name]
        collection = db[collection_name]

        deleted_count = 0
        if overwrite:
            slot_filter = {'season': year, 'week': week, 'timezone': timezone}

            # A game already stored as completed is frozen: never deleted,
            # never reinserted, regardless of what this run recomputed for
            # it. Only clear the not-yet-completed docs in this slot, not
            # just ones matching games present in the new data -- so a game
            # that drops out of this run (e.g. loses its outlet) gets
            # cleaned up instead of left behind as a stale record.
            frozen_ids = set(collection.distinct('game_id', {**slot_filter, 'completed': True}))
            delete_result = collection.delete_many({**slot_filter, 'completed': {'$ne': True}})
            deleted_count = delete_result.deleted_count
            data = [g for g in data if g.get('game_id') not in frozen_ids]

        # Insert new data and get inserted IDs
        if data:
            collection.insert_many(data)

        # Create a copy of the data with '_id' field removed
        clean_data = []
        for item in data:
            item_copy = item.copy()
            if '_id' in item_copy:
                del item_copy['_id']
            clean_data.append(item_copy)

        return clean_data, deleted_count

    finally:
        client.close()
