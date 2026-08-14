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
        overwrite (bool): If True, delete existing data first. If False, append to existing data.

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
            # Delete existing records that match season, week, away_team, and home_team from new data
            delete_conditions = []
            for game in data:
                if 'away_team' in game and 'home_team' in game:
                    delete_conditions.append({
                        'season': year,
                        'week': week,
                        'away_team': game['away_team'],
                        'home_team': game['home_team'],
                        'timezone': game['timezone']
                    })

            if delete_conditions:
                delete_result = collection.delete_many({'$or': delete_conditions})
                deleted_count = delete_result.deleted_count

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
