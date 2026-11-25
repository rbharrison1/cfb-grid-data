# cbb — college basketball pipeline

Feeds the college basketball grid at [cfbgrid.com/cbb](https://cfbgrid.com/cbb). It has the same shape as the football pipeline in the rest of this repo, but its source is the [CollegeBasketballData API](https://collegebasketballdata.com) (CBBD, from the same author as CFBD), and it navigates by **date** instead of by week.

```
Cloud Scheduler --> Cloud Run: cfb-grid-cbb-ingest (cbb/ingest)
                       |-- CBBD /games + /games/media (per date range, monthly chunks)
                       |-- CBBD /rankings (AP), /teams
                       |-- ncaa.com NET page (daily snapshot, 1 request; T-Rank paused, see below)
                       |-- NDJSON to gs://<bucket>/raw/cbb/<key>/season=<S>/<range>/<ts>.json
                       `-- WRITE_APPEND into BigQuery dataset cbb_raw, tables raw_<key>

Cloud Scheduler --> Cloud Run: cfb-grid-cbb-transform (cbb/transform)
                       |-- read cbb_raw.raw_* (latest _ingested_at per key)
                       |-- outlet -> column, team names/ids/colors, AP rank, W-L record, 6 timezone copies
                       |-- game modal fields: venue, per-half line scores, attendance, notes, schedule window,
                       |   T-Rank/NET/quad records as of the game's date
                       `-- rewrite each slate date in MongoDB cfb-grid.cbb-games
```

`cfb-grid-server`'s `routes/cbbGame.mjs` serves `cbb-games` at `/cbb/game`. Both services are private and are invoked with an OIDC identity token.

## Endpoints and params

**Ingest: `POST /ingest`**
- `season`: CBBD numbering, which is the year the season *ends* (2026-27 = `2027`). Default env `DEFAULT_SEASON`.
- `start` / `end`: Eastern `YYYY-MM-DD`. Default is yesterday through +21 days.
- `range=season`: Nov 1 to Apr 15. Ingests in calendar-month chunks and also fetches `/teams`.
- `teams`: any value; refreshes `/teams` on a date-range run.
- `ratings=false`: skip the T-Rank/NET snapshots. They are taken by default only on in-season date-range runs (not `range=season`), so the daily job is the only thing that fetches them.
- `dataset` / `bucket`: override env `BQ_DATASET` / `GCS_BUCKET`.

**Transform: `POST /transform`**
- `season`, `start`, `end`, `range=season`, `dataset`: same meaning as ingest.
- `collection`: default env `MONGO_COLLECTION`. If that's unset, it defaults to **`cbb-games_test`**, so production has to be opted into.
- The response includes:
  - `games` (written), `no_broadcast` (written with `outlet`/`col` null; listed under the site's "Not Televised & TBD" section)
  - `unmapped_outlets`: broadcast names not in `config/networks.py`; watch this
  - `teams_without_id`: opponents with no logo id
  - `ratings_unmatched`: T-Rank/NET team names that matched no CBBD team; add them to `config/ratings_aliases.py`
  - `ratings_snapshot_rows`, plus `ratings_error` if the ratings tables couldn't be read (games are still written, without ratings)
  - per-date write counts

**`POST /ensure-indexes`**: idempotent. Optional `collection` param (default `cbb-games`).

## Schedule (Cloud Scheduler, America/New_York)

| Job | When | Call |
|---|---|---|
| `cfb-grid-cbb-daily-ingest` | 5:30 AM daily | `/ingest` with defaults (yesterday .. +21d; 3 API calls) |
| `cfb-grid-cbb-daily-transform` | 6:40 AM daily | `/transform` with defaults |
| `cfb-grid-cbb-weekly-ingest` | 4:30 AM Mon | `/ingest` `{"range":"season"}` (~13 API calls) |
| `cfb-grid-cbb-weekly-transform` | 4:50 AM Mon | `/transform` `{"range":"season"}` (~1 min) |

**API quota:** the CBBD quota is shared with football's CFBD key, about 1,000 calls/month on the free tier. Football's daily ingest uses roughly 210/month and this schedule roughly 140/month. The T-Rank/NET fetches don't touch this quota.

There are **no live scores yet**. Finals arrive with the next morning's run. Phase 2 is a date-based NCAA basketball poller.

## Non-obvious things (all verified against real CBBD data, Oct 2026)

- **The CFBD API key works for CBBD.** The deploy workflow mounts the existing `CFBD_API_KEY` secret as `CBBD_API_KEY`.
- **`/games` and `/games/media` cap at 3,000 rows per call.** A busy month is about 1,600 games, so full-season requests are split by calendar month (`month_chunks`). The range params (`startDateRange`/`endDateRange`) are UTC timestamps; the ingest pads them to cover whole Eastern days, and the transform filters on the exact Eastern date.
- **`_strip_empty` is copied from the football ingest on purpose.** Every score/elo/period field is null until a game is played, and BigQuery autodetect would lock those columns as STRING (see the root `CLAUDE.md`). Because the stripped columns may not exist yet, `bq_reader.py` selects whole rows with `TO_JSON_STRING` instead of naming columns.
- **`slate_date` is the Eastern calendar date and the Mongo write key.** Writes are delete-then-insert per `(season, slate_date)`, covering all six timezone docs at once. Every date in the range is cleared, including dates with no games this run. A game already stored `completed: true` is frozen, the same as football.
- **TBD tip times:** CBBD sets `startTimeTbd: true` with a midnight-Eastern placeholder (`05:00Z`). The transform keeps the slate date in every timezone copy (converting it would push the game to the previous day out West). It stores `time: "TBD"`, `start: null`, and the game's real `col`. cfb-grid-web lists any `start: null` game in its "Not Televised & TBD" section. Before 2026-11-17 these games were forced into the streaming section (`col: 18`).
- **Outlet mapping (`config/networks.py`):**
  - It was built from a full inventory of every `broadcastName` CBBD returned for 2025-26 plus Nov 2026: 92 names across ~7,900 games, with ~14% of games having no broadcast at all.
  - **That ~14% is for games already played. Future games are mostly unlisted.** CBBD fills in `broadcasts` as networks are announced, a few weeks out. As of 2026-11-17, Feb 2027 had 1,100 of 1,338 games with empty `broadcasts` (Dec and Jan were similar). Only the Monday `range=season` transform rewrites dates past the daily run's 3-week window, so any change to how those games are handled doesn't reach Dec–Mar until that job runs, or until you trigger `cfb-grid-cbb-weekly-transform` by hand. A report of "missing games" far ahead should start here: on 2026-11-17, February Saturdays showed about 30 games where ~150 exist, because no-broadcast games were still being dropped.
  - Column numbers reuse football's where the network is the same (12 = BTN overflow, 18 = streaming section).
  - Unlike football, an outlet with no column is **not dropped**. ESPN+, conference platforms, regional sports networks and local stations all go to the streaming section; only radio-only broadcasts are ignored.
  - A game with no broadcast at all is also kept, with `outlet`/`col` null; before 2026-11-17 it was dropped. `resolve_slot_conflicts` skips null-column games.
  - For a game with several broadcasts, a column network wins over a streaming one (e.g. Army @ Duke: ESPNU over Patriot League TV).
- **Team identity:** CBBD's `/teams` (D1 only, 365 teams) is the primary source. Its `sourceId` is the ESPN id, which is also the logo filename in `cfb-grid-web/public/logos/`.
  - `team_data/teams.json`'s name/color is applied on top **only when its `espn_id` matches**. Its football-oriented aliases aren't always right for basketball; for example, `"Little Rock"` is an alias of Arkansas Baptist there.
  - D1 names not covered by `teams.json` use `display_name()`: "State" becomes "St.", and CBBD's `shortDisplayName` is used when a name is still over 15 characters.
  - Non-D1 opponents (about 260 a season) fall back to `teams.json` by name, else get no logo or color.
- **Records** are computed from the season's final games in `raw_games`; CBBD has no `/records` endpoint. `bq_reader.read_season_games` reads every game of the season (any status, latest snapshot) for both this and the schedule window below.
- **Game modal fields** (added 2026-11-17, read by `cfb-grid-web`'s GameModal): `venue_name`/`city`/`state`, `conference_game`, `attendance`, `excitement`, `notes` (CBBD `gameNotes`, e.g. a tournament name), and `away_line_scores`/`home_line_scores` from CBBD's `*PeriodPoints` (halves plus any OTs, **final games only**; a scheduled game's snapshot can carry 0-filled arrays). `away_team_schedule`/`home_team_schedule` are a **window**, not the season: the 5 games before this one and the 3 after, by tip-off, this game left out (`ScheduleIndex` in `cbb_transform.py`; cancelled/postponed games skipped). Entries are `{game_id, date (Eastern slate date), opponent, opponent_id (ESPN id), opponent_color, location, result, score}`, with the score from the team's side. A non-D1 opponent with only one game in the data gets an empty window. No spread or win probability: neither is ingested for basketball. These fields, unlike scores, are refreshed on frozen completed games every run (`MODAL_REFRESH_FIELDS` in `mongo_writer.py`).
- **Team ratings (added 2026-11-24): T-Rank and NET, no KenPom.** KenPom is paywalled and its terms don't allow showing it on a public site, so it was left out on purpose. cbbdata (the R package/API that mirrors these sources) was also passed over: no commits since Oct 2024, and its docs domain no longer resolves.
  - **T-Rank fetch paused (2026-11-25).** barttorvik.com returns **403 Forbidden** to requests from Cloud Run, though the same request from a home connection works, so it reads as a deliberate block on server traffic. The direct Torvik fetch is off in `bq_ingest.py` and must not be worked around (no browser spoofing or proxies). T-Rank is moving to the cbbdata API, which mirrors Torvik's data. Until then `raw_torvik_ratings` gets no new rows and the T-Rank rows show "—".
  - **Sources and politeness** (`ingest/ratings_ingest.py`), as designed: `https://barttorvik.com/<season>_team_results.csv` and `https://www.ncaa.com/rankings/basketball-men/d1/ncaa-mens-basketball-net-rankings`. Both are allowed by their robots.txt (Torvik disallows `*.json` and some `.php` pages, and asks for `Crawl-Delay: 10`). The ingest sends one request per site per day with a `cfbgrid.com` User-Agent and retries once on 429/5xx, waiting at least 10s. A Torvik response that isn't CSV (a bot check) is an error and is never worked around. Torvik's time-machine pages are disallowed, so **there's no backfill**: games before the first snapshot (2026-11-24 onward) have no ratings.
  - **Tables:** `raw_torvik_ratings` (`torvik_rank, team, conf, record, adj_oe, adj_oe_rank, adj_de, adj_de_rank, barthag, wab, adj_t, adj_t_rank`; the CSV has two `rank` columns, so T-Rank is read by position. The off/def ranks are Torvik's own columns; the CSV has **no tempo rank**, so `adj_t_rank` is derived at ingest from the same snapshot, fastest = 1, ties sharing the better rank) and `raw_net_rankings` (`net_rank, school, record, conf, road, neutral, home, prev, q1..q4, through_date`). Each daily run is one snapshot. If a day has several ingests, the readers keep that day's **earliest**, so a mid-day re-run can't leak post-tip ratings into that day's games.
  - **Stale NET:** until the NCAA releases the season's first NET (usually early December), ncaa.com still shows last season's final; on 2026-11-24 it said "Through Games Apr. 06 2026". `read_net_snapshots` drops rows whose `through_date` is before Nov 1 of `season - 1`, so the NET row shows "—" until the real one lands.
  - **Matching** (`transform/ratings_match.py`): neither source shares an id with CBBD, so names are matched. First an explicit alias in `config/ratings_aliases.py` (basketball-only, not `teams.json`), then CBBD `school`, `shortDisplayName`, `abbreviation` after normalizing case, punctuation, accents, and State/St./Saint. A name that matches more than one team counts as unmatched. On the 2026-11-24 data, all 365 Torvik teams and every NET team still in D1 matched (`test_ratings.py`).
  - **Frozen at tip-off:** a game gets the latest snapshot dated on or before its `slate_date`, not the latest overall. The 5:30 AM ingest is before any tip, so that's the rating going into the game, and re-transforms of past dates keep picking the same snapshot. On the doc: `<side>_stats = {torvik_rank, torvik_adj_em, torvik_adj_oe, torvik_adj_oe_rank, torvik_adj_de, torvik_adj_de_rank, torvik_adj_t, torvik_adj_t_rank, torvik_barthag, net_rank, quad_records: {q1..q4}}` (missing keys left out, null if nothing matched) and `ratings_as_of = {torvik: snapshot date, net: through date}`. These are deliberately **not** in `MODAL_REFRESH_FIELDS`; completed games keep what they had.
- **AP rank** is the latest poll whose `pollDate` is on or before the game's slate date. CBBD's games carry no week, so football's week-matching doesn't apply. Rows with a null `ranking` are "receiving votes" and are dropped.
- **Grid geometry:** `GAME_SLOTS = 4` (2-hour blocks). `cfb-grid-web`'s `config/sports.js` CBB `gameSlots` must match. A second game starting less than 4 slots after the previous one in the same column is nudged down (its printed time is unchanged). An exact same-start collision (regional feeds) sends the second game to the BTN overflow column if it's BTN, otherwise to the streaming section.
