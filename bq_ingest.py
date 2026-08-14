import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from google.cloud import bigquery, storage

import fbschedules

CFBD_API_BASE = "https://api.collegefootballdata.com"

# Sentinel for 'week=all' requests. Using an int (rather than None/NULL) keeps
# the _week column's BigQuery type stable: a batch where every row has _week
# NULL can't have its type inferred by autodetect and falls back to STRING,
# which then conflicts with the INTEGER type already established by prior
# per-week loads. -1 never collides with a real week number.
FULL_SEASON_WEEK = -1


def _year_week_params(year, week):
    """CFBD accepts an omitted week param on games/media/rankings/win_prob to
    return every week of the season in one call -- used for full-season
    ('week=all') requests."""
    params = {"year": year}
    if week != FULL_SEASON_WEEK:
        params["week"] = week
    return params


# key -> fn(year, week) -> (cfbd endpoint path, query params). week may be None
# to request the full season in one call (see _year_week_params).
ENDPOINTS = {
    "games": lambda year, week: ("games", _year_week_params(year, week)),
    "media": lambda year, week: ("games/media", _year_week_params(year, week)),
    "records": lambda year, week: ("records", {"year": year}),
    "rankings": lambda year, week: ("rankings", _year_week_params(year, week)),
    "venues": lambda year, week: ("venues", {}),
    "all_games": lambda year, week: ("games", {"year": year}),
    "win_prob": lambda year, week: ("metrics/wp/pregame", _year_week_params(year, week)),
}


def _fetch_endpoint(api_key: str, endpoint: str, params: Dict[str, Any] = None) -> List[dict]:
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    url = f"{CFBD_API_BASE}/{endpoint}"
    resp = requests.get(url, params=params or {}, headers=headers, timeout=30)
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


def _land_and_load_rows(
    storage_client: storage.Client,
    bq_client: bigquery.Client,
    project: str,
    dataset: str,
    bucket_name: str,
    key: str,
    rows: List[dict],
    source_endpoint: str,
    year: int,
    week: int,
    run_ts: str,
    ingested_at: str,
) -> Dict[str, Any]:
    """Tags rows with ingestion metadata, lands them in GCS, then BQ-loads them
    into raw_<key>. Shared by both the CFBD endpoint loop and the fbschedules.com
    scrape below -- same landing pattern regardless of where the rows came from."""
    if not rows:
        return {"rows_loaded": 0}

    for row in rows:
        if isinstance(row, dict):
            row["_ingested_at"] = ingested_at
            row["_source_endpoint"] = source_endpoint
            row["_year"] = year
            row["_week"] = week

    week_label = "all" if week == FULL_SEASON_WEEK else week
    blob_path = f"raw/{key}/year={year}/week={week_label}/{run_ts}.json"
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


def _fetch_tv_schedule_rows(year: int, week: int) -> List[dict]:
    if week == FULL_SEASON_WEEK:
        return fbschedules.fetch_full_season(year)
    return fbschedules.fetch_tv_schedule(year, week)


def bq_ingest(request):
    """HTTP entrypoint — land raw CFBD endpoint data in GCS, then append-load into raw BigQuery tables.

    Query params / JSON body:
      - year (int)
      - week (int, or 'all' to land every week of the season in one call)
      - dataset (BigQuery dataset name, required)
      - bucket (GCS landing bucket, required)

    Environment:
      - CFBD_API_KEY (inject via Secret Manager)
      - BQ_DATASET / GCS_BUCKET (defaults if not passed as params)
      - BQ_PROJECT (optional, defaults to the ambient project)
      - BQ_LOCATION (optional, defaults to US)
      - DEFAULT_YEAR / DEFAULT_WEEK (optional)
    """
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    def _param(name, default=None):
        return request_args.get(name) or request_json.get(name) or default

    year = int(_param("year", os.environ.get("DEFAULT_YEAR", 2025)))
    week_param = str(_param("week", os.environ.get("DEFAULT_WEEK", 1)))
    week = FULL_SEASON_WEEK if week_param.strip().lower() == "all" else int(week_param)
    dataset = _param("dataset", os.environ.get("BQ_DATASET"))
    bucket_name = _param("bucket", os.environ.get("GCS_BUCKET"))

    if not dataset:
        return ("Missing BigQuery dataset. Provide 'dataset' param or set BQ_DATASET env var.", 400)
    if not bucket_name:
        return ("Missing GCS bucket. Provide 'bucket' param or set GCS_BUCKET env var.", 400)

    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        return ("Missing CFBD_API_KEY environment variable", 500)

    storage_client = storage.Client()
    bq_client = bigquery.Client(project=os.environ.get("BQ_PROJECT"))
    project = bq_client.project
    dataset_id = f"{project}.{dataset}"
    _ensure_dataset(bq_client, dataset_id)

    now = datetime.now(timezone.utc)
    run_ts = now.strftime("%Y%m%dT%H%M%SZ")
    ingested_at = now.isoformat()

    results = {}
    for key, build_params in ENDPOINTS.items():
        endpoint, params = build_params(year, week)

        try:
            rows = _fetch_endpoint(api_key, endpoint, params=params)
        except Exception as e:
            results[key] = {"error": f"fetch failed: {e}"}
            continue

        results[key] = _land_and_load_rows(
            storage_client, bq_client, project, dataset, bucket_name,
            key, rows, endpoint, year, week, run_ts, ingested_at,
        )

    # Supplemental scrape (not a CFBD endpoint): fbschedules.com's TV schedule,
    # used to fill network/outlet gaps CFBD's games/media doesn't cover. Never
    # allowed to fail the rest of the ingest run -- it's a best-effort scrape
    # of a third-party site with no SLA.
    try:
        tv_rows = _fetch_tv_schedule_rows(year, week)
    except Exception as e:
        results["tv_schedule"] = {"error": f"fetch failed: {e}"}
    else:
        results["tv_schedule"] = _land_and_load_rows(
            storage_client, bq_client, project, dataset, bucket_name,
            "tv_schedule", tv_rows, "tv_schedule", year, week, run_ts, ingested_at,
        )

    return (json.dumps(results), 200)


def health_check(request):
    return ("OK", 200)
