"""Patches live_* fields onto existing Mongo game documents.

Deliberately a separate, live_-prefixed field group (live_away_points,
live_home_points, live_period, live_clock, live_status, live_updated_at)
rather than writing into away_points/home_points/completed -- those are
CFBD/daily-batch-owned fields (see transform/bq_transform.py's
format_game_data and transform/mongo_writer.py's completed-freeze logic).
Keeping this service's writes in their own namespace means there's no write
collision and no ambiguity about which pipeline last touched a field.

Patches via $set with pymongo's UpdateMany, not the delete-then-insert
pattern transform/mongo_writer.py uses for its full-slot batch writes -- this
service only ever adds/refreshes a handful of fields on documents that
already exist; it never creates or deletes documents.

IMPORTANT interaction with the daily batch transform, documented not fixed:
transform/mongo_writer.py's completed-freeze logic only protects documents
already `completed: true` BEFORE a given day's transform run starts. So a
game patched with live_* fields today (still completed: false in Mongo)
gets fully replaced tomorrow morning -- deleted and reinserted without any
live_* fields -- once CFBD's daily batch marks it completed: true, since it
wasn't frozen before that run began. This is expected: live_* is a
transient, best-effort overlay that only needs to exist from kickoff until
the next morning, at which point CFBD's authoritative final data takes over
via the normal pipeline.
"""

from typing import List

from pymongo import UpdateMany
from pymongo.collection import Collection


def patch_live_games(collection: Collection, season: int, matched_games: List[dict]) -> dict:
    """One UpdateMany per matched game, sent as a single bulk_write instead
    of one round trip per game -- at typical Saturday-afternoon volume
    (60-80 live FBS games at once) even a naive per-game loop would very
    likely finish in seconds inside a 2-minute poll window, so this isn't
    fixing an actual timeout risk. bulk_write(ordered=False) is still
    strictly better at no added complexity: fewer round trips, and one bad
    op doesn't stop the rest of the batch.

    Each UpdateMany's filter matches only on game_id + season -- deliberately
    NOT week. team_match.build_game_lookup already resolves the correct
    game_id against whichever Mongo week slot(s) actually hold it (week=1
    polls also check week=0, since bq_transform.py's _split_off_week_zero
    re-buckets some of CFBD's week=1 games into Mongo's week=0 by kickoff
    date -- see build_game_lookup's docstring). Re-adding a `week` filter
    here using NCAA's reported week would silently fail to match a game
    Mongo actually filed under week=0 -- confirmed by hand (2026 week 1):
    "Hawaii Stanford" and "Memphis UNLV" both resolved by name but the
    update matched zero documents until this filter was scoped to game_id
    alone. game_id is CFBD's own id and only ever lives in one real week's
    slot, so dropping `week` from the filter doesn't risk matching a
    different game.

    Patches all 6 timezone-fanout docs sharing that game_id in one operation
    (transform/mongo_writer.py's doc model: one doc per (game_id, timezone)).

    Idempotent -- safe to re-run every poll cycle for a still-live or
    already-final game with no separate "already wrote this" tracking.
    """
    if not matched_games:
        return {"matched_count": 0, "modified_count": 0}

    operations = [
        UpdateMany(
            {"game_id": game["game_id"], "season": season},
            {"$set": {k: v for k, v in game.items() if k != "game_id"}},
        )
        for game in matched_games
    ]
    result = collection.bulk_write(operations, ordered=False)
    return {"matched_count": result.matched_count, "modified_count": result.modified_count}
