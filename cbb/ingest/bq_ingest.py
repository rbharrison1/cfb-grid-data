"""College basketball ingest: CollegeBasketballData (CBBD) API -> GCS -> BigQuery raw_* tables.

Mirrors the football ingest (../../bq_ingest.py) -- same land-in-GCS-then-
WRITE_APPEND-load pattern, same _strip_empty guard -- but basketball is
navigated by date, not week, so every request covers an Eastern date range
instead of a week number. The landing/BigQuery helpers below are copied from
the football ingest rather than imported: each Cloud Run service builds from
its own directory.

CBBD caps /games and /games/media at 3,000 rows per call, so a full-season
request is split into calendar-month chunks (a busy November is ~1,600 games).
"""

import os
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import requests

from google.cloud import bigquery, storage

CBBD_API_BASE = "https://api.collegebasketballdata.com"

EASTERN = ZoneInfo("America/New_York")

# Default rolling window for the daily job: yesterday (to pick up final
# scores) through three weeks out (to pick up newly-announced TV assignments).
DEFAULT_WINDOW_DAYS_BACK = 1
DEFAULT_WINDOW_DAYS_AHEAD = 21

# key -> CBBD endpoint, fetched once per date chunk.
DATE_RANGE_ENDPOINTS = {
    "games": "games",
    "media": "games/media",
}


def _fetch_endpoint(api_key: str, endpoint: str, params: Dict[str, Any] = None) -> List[dict]:
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    url = f"{CBBD_API_BASE}/{endpoint}"
    resp = requests.get(url, params=params or {}, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


def _write_ndjson_to_gcs(storage_client: storage.Client, bucket_name: str, blob_path: str, rows: List[dict]) -> str:
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    body = "\n".join(json.dumps(row) for row in rows)
    blob.upload_from_string(body, content_type="application/json")
    return f"gs://{bucket_name}/{blob_path}"


def _ensure_dataset(client: bigquery.Client, dataset_id: str):
    try:
        client.get_dataset(dataset_id)
    except Exception:
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = os.environ.get("BQ_LOCATION", "US")
        client.create_dataset(dataset, exists_ok=True)


def _load_gcs_to_bq(client: bigquery.Client, gcs_uri: str, table_id: str) -> int:
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        autodetect=True,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
    )
    load_job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()
    table = client.get_table(table_id)
    return table.num_rows


def _strip_empty(value):
    """Recursively drops dict keys whose value is None or an empty list.

    Same guard as the football ingest (see cfb-grid-data's CLAUDE.md): BigQuery
    autodetect locks an all-null column to STRING forever, and every CBBD
    score/elo/period-points field is null until a game is played. Dropping the
    key means the column is first typed when a real value shows up."""
    if isinstance(value, dict):
        return {k: _strip_empty(v) for k, v in value.items() if v is not None and v != []}
    if isinstance(value, list):
        return [_strip_empty(v) for v in value]
    return value


def _land_and_load_rows(
    storage_client: storage.Client,
    bq_client: bigquery.Client,
    project: str,
    dataset: str,
    bucket_name: str,
    key: str,
    rows: List[dict],
    source_endpoint: str,
    season: int,
    range_label: str,
    run_ts: str,
    ingested_at: str,
) -> Dict[str, Any]:
    """Tags rows with ingestion metadata, lands them in GCS, then BQ-loads them
    into raw_<key>."""
    if not rows:
        return {"rows_loaded": 0}

    rows = [_strip_empty(row) if isinstance(row, dict) else row for row in rows]
    for row in rows:
        if isinstance(row, dict):
            row["_ingested_at"] = ingested_at
            row["_source_endpoint"] = source_endpoint
            row["_season"] = season
            row["_range"] = range_label

    blob_path = f"raw/cbb/{key}/season={season}/{range_label}/{run_ts}.json"
    try:
        gcs_uri = _write_ndjson_to_gcs(storage_client, bucket_name, blob_path, rows)
    except Exception as e:
        return {"error": f"gcs landing failed: {e}"}

    table_id = f"{project}.{dataset}.raw_{key}"
    try:
        table_total_rows = _load_gcs_to_bq(bq_client, gcs_uri, table_id)
        return {
            "rows_loaded": len(rows),
            "table": table_id,
            "table_total_rows": table_total_rows,
            "gcs_uri": gcs_uri,
        }
    except Exception as e:
        return {"error": f"bq load failed: {e}", "gcs_uri": gcs_uri}


def eastern_today() -> date:
    return datetime.now(EASTERN).date()


def season_bounds(season: int) -> Tuple[date, date]:
    """CBBD numbers a season by the year it ends in (2026-27 is season=2027).
    Nov 1 through mid-April covers the regular season and every postseason
    tournament, with margin on both ends."""
    return date(season - 1, 11, 1), date(season, 4, 15)


def month_chunks(start: date, end: date) -> List[Tuple[date, date]]:
    """Splits [start, end] (inclusive) at calendar-month boundaries, keeping
    each CBBD call well under its 3,000-row cap."""
    chunks = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(end, next_month - timedelta(days=1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def date_range_params(season: int, start: date, end: date) -> Dict[str, Any]:
    """CBBD's startDateRange/endDateRange are UTC timestamps. An Eastern
    calendar day runs from 04:00/05:00Z to 04:00/05:00Z the next day, so the
    UTC window is padded to fully cover [start, end] in Eastern time -- the
    transform filters on the exact Eastern date anyway, so a little extra on
    either side is harmless."""
    return {
        "season": season,
        "startDateRange": f"{start.isoformat()}T00:00:00Z",
        "endDateRange": f"{(end + timedelta(days=1)).isoformat()}T12:00:00Z",
    }


def bq_ingest(request):
    """HTTP entrypoint -- land raw CBBD data in GCS, then append-load into raw BigQuery tables.

    Query params / JSON body:
      - season (int, CBBD numbering -- the year the season ends, e.g. 2027 for 2026-27)
      - start / end (YYYY-MM-DD Eastern dates; default yesterday .. +21 days)
      - range ('season' to ingest the whole season in monthly chunks, plus /teams)
      - teams (any value: also refresh /teams on a date-range run)
      - dataset (BigQuery dataset name, required)
      - bucket (GCS landing bucket, required)

    Environment:
      - CBBD_API_KEY (inject via Secret Manager -- the CFBD key works for CBBD too)
      - BQ_DATASET / GCS_BUCKET (defaults if not passed as params)
      - BQ_PROJECT (optional, defaults to the ambient project)
      - BQ_LOCATION (optional, defaults to US)
      - DEFAULT_SEASON (optional)
    """
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    def _param(name, default=None):
        value = request_args.get(name)
        if value is None:
            value = request_json.get(name)
        return default if value is None else value

    season = int(_param("season", os.environ.get("DEFAULT_SEASON", 2027)))
    full_season = str(_param("range", "")).strip().lower() == "season"
    dataset = _param("dataset", os.environ.get("BQ_DATASET"))
    bucket_name = _param("bucket", os.environ.get("GCS_BUCKET"))

    if not dataset:
        return ("Missing BigQuery dataset. Provide 'dataset' param or set BQ_DATASET env var.", 400)
    if not bucket_name:
        return ("Missing GCS bucket. Provide 'bucket' param or set GCS_BUCKET env var.", 400)

    if full_season:
        start, end = season_bounds(season)
    else:
        today = eastern_today()
        try:
            start = date.fromisoformat(_param("start", (today - timedelta(days=DEFAULT_WINDOW_DAYS_BACK)).isoformat()))
            end = date.fromisoformat(_param("end", (today + timedelta(days=DEFAULT_WINDOW_DAYS_AHEAD)).isoformat()))
        except ValueError as e:
            return (f"Invalid start/end date: {e}", 400)
        if end < start:
            return ("'end' must be on or after 'start'", 400)

    api_key = os.environ.get("CBBD_API_KEY")
    if not api_key:
        return ("Missing CBBD_API_KEY environment variable", 500)

    storage_client = storage.Client()
    bq_client = bigquery.Client(project=os.environ.get("BQ_PROJECT"))
    project = bq_client.project
    _ensure_dataset(bq_client, f"{project}.{dataset}")

    now = datetime.now(timezone.utc)
    run_ts = now.strftime("%Y%m%dT%H%M%SZ")
    ingested_at = now.isoformat()

    def land(key, rows, endpoint, range_label):
        return _land_and_load_rows(
            storage_client, bq_client, project, dataset, bucket_name,
            key, rows, endpoint, season, range_label, run_ts, ingested_at,
        )

    results = {"season": season, "start": start.isoformat(), "end": end.isoformat()}
    for chunk_start, chunk_end in month_chunks(start, end):
        range_label = f"{chunk_start.isoformat()}_{chunk_end.isoformat()}"
        params = date_range_params(season, chunk_start, chunk_end)
        for key, endpoint in DATE_RANGE_ENDPOINTS.items():
            try:
                rows = _fetch_endpoint(api_key, endpoint, params=params)
            except Exception as e:
                results[f"{key}:{range_label}"] = {"error": f"fetch failed: {e}"}
                continue
            results[f"{key}:{range_label}"] = land(key, rows, endpoint, range_label)

    # Season-wide endpoints. Rankings change weekly and are one cheap call, so
    # they're refreshed on every run; the team list barely changes all season.
    season_endpoints = {"rankings": ("rankings", {"season": season, "pollType": "ap"})}
    if full_season or _param("teams") is not None:
        season_endpoints["teams"] = ("teams", {"season": season})

    for key, (endpoint, params) in season_endpoints.items():
        try:
            rows = _fetch_endpoint(api_key, endpoint, params=params)
        except Exception as e:
            results[key] = {"error": f"fetch failed: {e}"}
            continue
        results[key] = land(key, rows, endpoint, "season")

    return (json.dumps(results), 200)


def health_check(request):
    return ("OK", 200)
