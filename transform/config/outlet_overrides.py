"""Hand-set TV outlets that beat both fbschedules.com and CFBD.

Keyed by CFBD game id (the `game_id` on every Mongo document); the value must
be a NETWORK_COLUMNS key or the game gets dropped from the grid. Applied in
bq_transform.py right after the fbschedules/CFBD outlet merge. Only for games
both sources have wrong -- remove an entry once the sources catch up, or at
the end of the season.
"""

OUTLET_OVERRIDES = {
    401856751: 'SECN',  # 2026 wk12 Kentucky @ Missouri -- fbschedules says ESPN
}
