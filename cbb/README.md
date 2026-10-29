# cbb — college basketball pipeline

Feeds the college basketball grid at [cfbgrid.com/cbb](https://cfbgrid.com/cbb). It has the same shape as the football pipeline in the rest of this repo, but its source is the [CollegeBasketballData API](https://collegebasketballdata.com) (CBBD, from the same author as CFBD), and it navigates by **date** instead of by week.

```
Cloud Scheduler --> Cloud Run: cfb-grid-cbb-ingest (cbb/ingest)
                       |-- CBBD /games + /games/media (per date range, monthly chunks)
                       |-- CBBD /rankings (AP), /teams
                       |-- NDJSON to gs://<bucket>/raw/cbb/<key>/season=<S>/<range>/<ts>.json
                       `-- WRITE_APPEND into BigQuery dataset cbb_raw, tables raw_<key>

Cloud Scheduler --> Cloud Run: cfb-grid-cbb-transform (cbb/transform)
                       |-- read cbb_raw.raw_* (latest _ingested_at per key)
                       |-- outlet -> column, team names/ids/colors, AP rank, W-L record, 6 timezone copies
                       `-- rewrite each slate date in MongoDB cfb-grid.cbb-games
```

`cfb-grid-server`'s `routes/cbbGame.mjs` serves `cbb-games` at `/cbb/game`. Both services are private and are invoked with an OIDC identity token.

## Endpoints and params

**Ingest: `POST /ingest`**
- `season`: CBBD numbering, which is the year the season *ends* (2026-27 = `2027`). Default env `DEFAULT_SEASON`.
- `start` / `end`: Eastern `YYYY-MM-DD`. Default is yesterday through +21 days.
- `range=season`: Nov 1 to Apr 15. Ingests in calendar-month chunks and also fetches `/teams`.
- `teams`: any value; refreshes `/teams` on a date-range run.
- `dataset` / `bucket`: override env `BQ_DATASET` / `GCS_BUCKET`.

**Transform: `POST /transform`**
- `season`, `start`, `end`, `range=season`, `dataset`: same meaning as ingest.
- `collection`: default env `MONGO_COLLECTION`. If that's unset, it defaults to **`cbb-games_test`**, so production has to be opted into.
- The response includes:
  - `games` (written), `no_broadcast` (dropped)
  - `unmapped_outlets`: broadcast names not in `config/networks.py`; watch this
  - `teams_without_id`: opponents with no logo id
  - per-date write counts

**`POST /ensure-indexes`**: idempotent. Optional `collection` param (default `cbb-games`).

## Schedule (Cloud Scheduler, America/New_York)

| Job | When | Call |
|---|---|---|
| `cfb-grid-cbb-daily-ingest` | 5:30 AM daily | `/ingest` with defaults (yesterday .. +21d; 3 API calls) |
| `cfb-grid-cbb-daily-transform` | 6:40 AM daily | `/transform` with defaults |
| `cfb-grid-cbb-weekly-ingest` | 4:30 AM Mon | `/ingest` `{"range":"season"}` (~13 API calls) |
| `cfb-grid-cbb-weekly-transform` | 4:50 AM Mon | `/transform` `{"range":"season"}` (~1 min) |

**API quota:** the CBBD quota is shared with football's CFBD key, about 1,000 calls/month on the free tier. Football's daily ingest uses roughly 210/month and this schedule roughly 140/month.

There are **no live scores yet**. Finals arrive with the next morning's run. Phase 2 is a date-based NCAA basketball poller.

## Non-obvious things (all verified against real CBBD data, Oct 2026)

- **The CFBD API key works for CBBD.** The deploy workflow mounts the existing `CFBD_API_KEY` secret as `CBBD_API_KEY`.
- **`/games` and `/games/media` cap at 3,000 rows per call.** A busy month is about 1,600 games, so full-season requests are split by calendar month (`month_chunks`). The range params (`startDateRange`/`endDateRange`) are UTC timestamps; the ingest pads them to cover whole Eastern days, and the transform filters on the exact Eastern date.
- **`_strip_empty` is copied from the football ingest on purpose.** Every score/elo/period field is null until a game is played, and BigQuery autodetect would lock those columns as STRING (see the root `CLAUDE.md`). Because the stripped columns may not exist yet, `bq_reader.py` selects whole rows with `TO_JSON_STRING` instead of naming columns.
- **`slate_date` is the Eastern calendar date and the Mongo write key.** Writes are delete-then-insert per `(season, slate_date)`, covering all six timezone docs at once. Every date in the range is cleared, including dates with no games this run. A game already stored `completed: true` is frozen, the same as football.
- **TBD tip times:** CBBD sets `startTimeTbd: true` with a midnight-Eastern placeholder (`05:00Z`). The transform keeps the slate date in every timezone copy (converting it would push the game to the previous day out West). It stores `time: "TBD"`, `start: null`, and puts the game in the streaming section.
- **Outlet mapping (`config/networks.py`):**
  - It was built from a full inventory of every `broadcastName` CBBD returned for 2025-26 plus Nov 2026: 92 names across ~7,900 games, with ~14% of games having no broadcast at all.
  - Column numbers reuse football's where the network is the same (12 = BTN overflow, 18 = streaming section).
  - Unlike football, an outlet with no column is **not dropped**. ESPN+, conference platforms, regional sports networks and local stations all go to the streaming section; only radio-only broadcasts are ignored.
  - For a game with several broadcasts, a column network wins over a streaming one (e.g. Army @ Duke: ESPNU over Patriot League TV).
- **Team identity:** CBBD's `/teams` (D1 only, 365 teams) is the primary source. Its `sourceId` is the ESPN id, which is also the logo filename in `cfb-grid-web/public/logos/`.
  - `team_data/teams.json`'s name/color is applied on top **only when its `espn_id` matches**. Its football-oriented aliases aren't always right for basketball; for example, `"Little Rock"` is an alias of Arkansas Baptist there.
  - D1 names not covered by `teams.json` use `display_name()`: "State" becomes "St.", and CBBD's `shortDisplayName` is used when a name is still over 15 characters.
  - Non-D1 opponents (about 260 a season) fall back to `teams.json` by name, else get no logo or color.
- **Records** are computed from the season's final games in `raw_games`; CBBD has no `/records` endpoint.
- **AP rank** is the latest poll whose `pollDate` is on or before the game's slate date. CBBD's games carry no week, so football's week-matching doesn't apply. Rows with a null `ranking` are "receiving votes" and are dropped.
- **Grid geometry:** `GAME_SLOTS = 4` (2-hour blocks). `cfb-grid-web`'s `config/sports.js` CBB `gameSlots` must match. A second game starting less than 4 slots after the previous one in the same column is nudged down (its printed time is unchanged). An exact same-start collision (regional feeds) sends the second game to the BTN overflow column if it's BTN, otherwise to the streaming section.
