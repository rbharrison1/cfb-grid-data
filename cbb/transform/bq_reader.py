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


def read_final_games(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """Every final game of the season (latest snapshot), for computing W-L
    records -- CBBD has no /records endpoint like CFBD's."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM (
          SELECT * FROM `{table}`
          WHERE season = @season
          QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
        ) t
        WHERE status = 'final'
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


def read_teams(client: bigquery.Client, table: str, season: int) -> List[dict]:
    """The season's D1 team list (CBBD id, ESPN sourceId, school, color, conference)."""
    sql = f"""
        SELECT TO_JSON_STRING(t) AS j
        FROM `{table}` t
        WHERE _season = @season
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    return _rows(client, sql, [bigquery.ScalarQueryParameter("season", "INT64", season)])
