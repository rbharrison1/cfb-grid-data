import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from google.cloud import bigquery, storage

CFBD_API_BASE = "https://api.collegefootballdata.com"

# key -> fn(year, week) -> (cfbd endpoint path, query params)
ENDPOINTS = {
    "games": lambda year, week: ("games", {"year": year, "week": week}),
    "records": lambda year, week: ("records", {"year": year}),
    "rankings": lambda year, week: ("rankings", {"year": year, "week": week}),
    "venues": lambda year, week: ("venues", {}),
    "all_games": lambda year, week: ("games", {"year": year}),
    "win_prob": lambda year, week: ("metrics/wp/pregame", {"year": year, "week": week}),
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


def bq_ingest(request):
    """HTTP entrypoint — land raw CFBD endpoint data in GCS, then append-load into raw BigQuery tables.

    Query params / JSON body:
      - year (int)
      - week (int)
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
    week = int(_param("week", os.environ.get("DEFAULT_WEEK", 1)))
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

        for row in rows:
            if isinstance(row, dict):
                row["_ingested_at"] = ingested_at
                row["_source_endpoint"] = endpoint
                row["_year"] = year
                row["_week"] = week

        blob_path = f"raw/{key}/year={year}/week={week}/{run_ts}.json"
        try:
            gcs_uri = _write_ndjson_to_gcs(storage_client, bucket_name, blob_path, rows)
        except Exception as e:
            results[key] = {"error": f"gcs landing failed: {e}"}
            continue

        table_id = f"{project}.{dataset}.raw_{key}"
        try:
            table_total_rows = _load_gcs_to_bq(bq_client, gcs_uri, table_id)
            results[key] = {
                "rows_loaded": len(rows),
                "table": table_id,
                "table_total_rows": table_total_rows,
                "gcs_uri": gcs_uri,
            }
        except Exception as e:
            results[key] = {"error": f"bq load failed: {e}", "gcs_uri": gcs_uri}

    return (json.dumps(results), 200)


def health_check(request):
    return ("OK", 200)
