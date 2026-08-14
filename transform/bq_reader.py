"""Reads deduplicated rows out of the raw_* BigQuery landing tables.

Each raw_* table is WRITE_APPEND (see bq_ingest.py), so every ingestion run
appends a fresh full copy of rows tagged with _ingested_at/_year/_week. Every
query here dedupes to the latest _ingested_at per natural key before handing
data to the transform logic.

Week filtering uses each row's own embedded `week` field (from the CFBD
payload itself), not the `_week` ingestion tag -- bq_ingest.py supports a
'week=all' full-season ingest mode where `_week` is NULL for every landed
row regardless of which week that row's game/media/ranking/win-prob entry
actually belongs to. Filtering on the row's real `week` field works
correctly whether the data came from a per-week or a full-season ingest run.
"""

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery


def _query_params(year=None, week=None):
    params = []
    if year is not None:
        params.append(bigquery.ScalarQueryParameter("year", "INT64", year))
    if week is not None:
        params.append(bigquery.ScalarQueryParameter("week", "INT64", week))
    return params


def _run(client: bigquery.Client, sql: str, year=None, week=None):
    job_config = bigquery.QueryJobConfig(query_parameters=_query_params(year, week))
    return client.query(sql, job_config=job_config)


def _to_dataframe_or_empty(query_job, columns):
    """The raw_* table may not exist yet if that endpoint has never returned
    non-empty rows (e.g. 'records'/'media' before the season starts) -- treat
    that as 'no data yet', not an error."""
    try:
        return query_job.to_dataframe()
    except NotFound:
        return pd.DataFrame(columns=columns)


def _result_or_empty(query_job):
    try:
        return list(query_job.result())
    except NotFound:
        return []


def read_games(client: bigquery.Client, table: str, year: int, week: int) -> pd.DataFrame:
    sql = f"""
        SELECT id, season, week, startDate, venueId, awayTeam, homeTeam, awayId, homeId,
               awayPoints, homePoints, awayLineScores, homeLineScores, completed,
               awayClassification, awayConference, homeClassification, homeConference, seasonType
        FROM `{table}`
        WHERE _year = @year AND week = @week
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    columns = ["id", "season", "week", "startDate", "venueId", "awayTeam", "homeTeam", "awayId", "homeId",
               "awayPoints", "homePoints", "awayLineScores", "homeLineScores", "completed",
               "awayClassification", "awayConference", "homeClassification", "homeConference", "seasonType"]
    return _to_dataframe_or_empty(_run(client, sql, year=year, week=week), columns)


def read_media(client: bigquery.Client, table: str, year: int, week: int) -> pd.DataFrame:
    sql = f"""
        SELECT id, outlet
        FROM `{table}`
        WHERE _year = @year AND week = @week
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    return _to_dataframe_or_empty(_run(client, sql, year=year, week=week), ["id", "outlet"])


def read_tv_schedule(client: bigquery.Client, table: str, year: int, week: int) -> pd.DataFrame:
    """Reads the fbschedules.com TV schedule scrape. There's no stable id from
    this source, so dedup keys on (away_team, home_team, date) instead."""
    sql = f"""
        SELECT away_team, home_team, date, time, network
        FROM `{table}`
        WHERE _year = @year AND week = @week
        QUALIFY ROW_NUMBER() OVER (PARTITION BY away_team, home_team, date ORDER BY _ingested_at DESC) = 1
    """
    columns = ["away_team", "home_team", "date", "time", "network"]
    return _to_dataframe_or_empty(_run(client, sql, year=year, week=week), columns)


def read_venues(client: bigquery.Client, table: str) -> pd.DataFrame:
    sql = f"""
        SELECT id, name, city, state, countryCode
        FROM `{table}`
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    return _to_dataframe_or_empty(_run(client, sql), ["id", "name", "city", "state", "countryCode"])


def read_records(client: bigquery.Client, table: str, year: int) -> pd.DataFrame:
    sql = f"""
        SELECT team, total.wins AS wins, total.losses AS losses
        FROM `{table}`
        WHERE _year = @year
        QUALIFY ROW_NUMBER() OVER (PARTITION BY team ORDER BY _ingested_at DESC) = 1
    """
    return _to_dataframe_or_empty(_run(client, sql, year=year), ["team", "wins", "losses"])


def read_win_prob(client: bigquery.Client, table: str, year: int, week: int) -> pd.DataFrame:
    sql = f"""
        SELECT gameId, spread, homeWinProbability
        FROM `{table}`
        WHERE _year = @year AND week = @week
        QUALIFY ROW_NUMBER() OVER (PARTITION BY gameId ORDER BY _ingested_at DESC) = 1
    """
    return _to_dataframe_or_empty(_run(client, sql, year=year, week=week), ["gameId", "spread", "homeWinProbability"])


def read_playoff_committee_rankings(client: bigquery.Client, table: str, year: int, week: int) -> pd.DataFrame:
    """Returns a (rank, school) DataFrame for the 'Playoff Committee Rankings' poll
    of the exact requested year/week. Empty if that poll doesn't exist yet for
    this week (expected early in the season, before CFP rankings begin)."""
    sql = f"""
        SELECT polls
        FROM `{table}`
        WHERE _year = @year AND week = @week
        QUALIFY ROW_NUMBER() OVER (PARTITION BY week ORDER BY _ingested_at DESC) = 1
    """
    rows = _result_or_empty(_run(client, sql, year=year, week=week))
    if not rows:
        return pd.DataFrame(columns=["rank", "school"])

    polls = rows[0].get("polls") or []
    target_poll = next((p for p in polls if p.get("poll") == "Playoff Committee Rankings"), None)
    if not target_poll:
        return pd.DataFrame(columns=["rank", "school"])

    ranks = target_poll.get("ranks") or []
    if not ranks:
        return pd.DataFrame(columns=["rank", "school"])

    return pd.DataFrame([{"rank": r.get("rank"), "school": r.get("school")} for r in ranks])


def read_all_games(client: bigquery.Client, table: str, year: int) -> list:
    """Returns the full season's games (as of the most recent ingestion run for
    this year) as a list of dicts, ignoring _week entirely -- every ingest run
    re-fetches the whole season regardless of which week triggered it."""
    sql = f"""
        SELECT id, week, startDate, homeTeam, awayTeam, neutralSite, homePoints, awayPoints
        FROM `{table}`
        WHERE _year = @year
        QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) = 1
    """
    columns = ["id", "week", "startDate", "homeTeam", "awayTeam", "neutralSite", "homePoints", "awayPoints"]
    return _to_dataframe_or_empty(_run(client, sql, year=year), columns).to_dict("records")
