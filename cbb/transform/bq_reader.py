"""Reads deduplicated rows out of the college basketball raw_* BigQuery tables.

Same dedupe approach as the football reader (transform/bq_reader.py): every
ingest run WRITE_APPENDs a fresh copy of each row, so each query keeps only
the latest _ingested_at per natural key.

Rows are selected whole, as JSON (TO_JSON_STRING), rather than by column
list. The ingest strips null/empty fields before landing (see _strip_empty in
cbb/ingest/bq_ingest.py), so a column like `tournament` or `homeSeed` doesn't
exist in the table at all until some game first has a value for it -- naming
it in a SELECT would fail the whole query. Reading the row as a dict and using
.get() sidesteps that.
"""

import json
from datetime import date
from typing import Dict, List

from google.api_core.exceptions import NotFound
from google.cloud import bigquery


def _rows(client: bigquery.Client, sql: str, params: List[bigquery.ScalarQueryParameter]) -> List[dict]:
    """A raw_* table won't exist until its endpoint has returned at least one
    row -- treat that as 'no data yet', not an error."""
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        return [json.loads(row["j"]) for row in client.query(sql, job_config=job_config).result()]
    except NotFound:
        return []


def _date_range_params(season: int, start: date, end: date):
    return [
        bigquery.ScalarQueryParameter("season", "INT64", season),
        bigquery.ScalarQueryParameter("start", "DATE", start),
        bigquery.ScalarQueryParameter("end", "DATE", end),
    ]


def read_games(client: bigquery.Client, table: str, season: int, start: date, end: date) -> List[dict]:
    """Latest snapshot of every game whose Eastern tip-off date is in [start, end]."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM `{table}` t
        WHERE season = @season
          AND DATE(startDate, "America/New_York") BETWEEN @start AND @end
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    return _rows(client, sql, _date_range_params(season, start, end))


def read_media(client: bigquery.Client, table: str, season: int, start: date, end: date) -> Dict[int, List[dict]]:
    """gameId -> broadcasts list, for games in [start, end]. A game whose
    latest snapshot has no broadcasts (the field is stripped when empty) maps
    to an empty list."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM `{table}` t
        WHERE season = @season
          AND DATE(startDate, "America/New_York") BETWEEN @start AND @end
        QUALIFY ROW_NUMBER() OVER (PARTITION BY gameId ORDER BY _ingested_at DESC) = 1
    """
    return {row["gameId"]: row.get("broadcasts") or [] for row in _rows(client, sql, _date_range_params(season, start, end))}


def read_season_games(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """Every game of the season (latest snapshot), played or not. Finals give
    the W-L records (CBBD has no /records endpoint like CFBD's); the whole
    list gives each team's schedule window for the game modal, which needs
    games outside the [start, end] range being written."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM `{table}` t
        WHERE season = @season
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    return _rows(client, sql, [bigquery.ScalarQueryParameter("season", "INT64", season)])


def read_rankings(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """Every AP poll entry of the season with a real ranking (CBBD also
    returns 'receiving votes' rows with a null ranking -- those are dropped)."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM `{table}` t
        WHERE season = @season AND ranking IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY seasonType, week, teamId ORDER BY _ingested_at DESC
        ) = 1
    """
    return _rows(client, sql, [bigquery.ScalarQueryParameter("season", "INT64", season)])


def _snapshot_rows(client: bigquery.Client, sql: str, season: int) -> List[dict]:
    """Like _rows, plus each row's `snapshot_date` (ISO string) from column d."""
    job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("season", "INT64", season)])
    try:
        result = client.query(sql, job_config=job_config).result()
    except NotFound:
        return []
    return [{**json.loads(row["j"]), "snapshot_date": row["d"]} for row in result]


# One snapshot per Eastern day: if a day has more than one ingest (a manual
# re-run), keep its earliest, which is the one from before that day's games.
_EARLIEST_PER_DAY = """
    QUALIFY _ingested_at = MIN(_ingested_at) OVER (PARTITION BY DATE(_ingested_at, "America/New_York"))
"""


def read_torvik_snapshots(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """Every daily T-Rank snapshot of the season (see ratings_match.py)."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j, CAST(DATE(_ingested_at, "America/New_York") AS STRING) AS d
        FROM `{table}` t
        WHERE _season = @season
        {_EARLIEST_PER_DAY}
    """
    return _snapshot_rows(client, sql, season)


def read_net_snapshots(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """Every daily NET snapshot of the season. Until the NCAA publishes the
    season's first NET (usually early December), ncaa.com still serves the
    previous season's final rankings; rows whose 'through games' date is
    before this season's Nov 1 are those, and are dropped."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j, CAST(DATE(_ingested_at, "America/New_York") AS STRING) AS d
        FROM `{table}` t
        WHERE _season = @season
          AND SAFE_CAST(CAST(through_date AS STRING) AS DATE) >= DATE(@season - 1, 11, 1)
        {_EARLIEST_PER_DAY}
    """
    return _snapshot_rows(client, sql, season)


def read_teams(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """The season's D1 team list (CBBD id, ESPN sourceId, school, color, conference)."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM `{table}` t
        WHERE _season = @season
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    return _rows(client, sql, [bigquery.ScalarQueryParameter("season", "INT64", season)])
