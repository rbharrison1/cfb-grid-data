import os
import json
import requests
from typing import Any, Dict

from google.cloud import bigquery

CFBD_API_BASE = "https://api.collegefootballdata.com"


def _fetch_endpoint(api_key: str, endpoint: str, params: Dict[str, Any] = None):
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    url = f"{CFBD_API_BASE}/{endpoint}"
    resp = requests.get(url, params=params or {}, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _ensure_dataset(client: bigquery.Client, dataset_id: str):
    try:
        client.get_dataset(dataset_id)
    except Exception:
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = os.environ.get("BQ_LOCATION", "US")
        client.create_dataset(dataset, exists_ok=True)


def _load_json_to_bq(client: bigquery.Client, table_id: str, rows):
    # rows should be list[dict]
    if not isinstance(rows, list):
        rows = [rows]

    job_config = bigquery.LoadJobConfig()
    job_config.autodetect = True
    job_config.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE

    load_job = client.load_table_from_json(rows, table_id, job_config=job_config)
    load_job.result()
    table = client.get_table(table_id)
    return table.num_rows


def bq_ingest(request):
    """HTTP Cloud Function — ingest CFBD endpoints into raw BigQuery tables.

    Query params / JSON body:
      - year (int)
      - week (int)
      - dataset (BigQuery dataset name, required)

    Environment:
      - CFBD_API_KEY (or use Secret Manager to inject as env var)
      - BQ_PROJECT (optional, defaults to Cloud function project)
      - BQ_LOCATION (optional, defaults to US)
    """
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    year = int(request_args.get("year") or request_json.get("year") or os.environ.get("DEFAULT_YEAR", 2025))
    week = int(request_args.get("week") or request_json.get("week") or os.environ.get("DEFAULT_WEEK", 1))
    dataset = request_args.get("dataset") or request_json.get("dataset") or os.environ.get("BQ_DATASET")

    if not dataset:
        return ("Missing BigQuery dataset. Provide 'dataset' param or set BQ_DATASET env var.", 400)

    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        return ("Missing CFBD_API_KEY environment variable", 500)

    client = bigquery.Client(project=os.environ.get("BQ_PROJECT"))
    project = client.project
    dataset_id = f"{project}.{dataset}"
    _ensure_dataset(client, dataset_id)

    endpoints = {
        "games": (f"games", {"year": year, "week": week}),
        "records": (f"records", {"year": year}),
        "rankings": (f"rankings", {"year": year, "week": week}),
        "venues": (f"venues", {}),
        "all_games": (f"games", {"year": year}),
        "win_prob": (f"metrics/wp/pregame", {"year": year, "week": week}),
    }

    results = {}
    for key, (endpoint, params) in endpoints.items():
        try:
            data = _fetch_endpoint(api_key, endpoint, params=params)
        except Exception as e:
            results[key] = {"error": str(e)}
            continue

        table_id = f"{project}.{dataset}.raw_{key}"
        try:
            row_count = _load_json_to_bq(client, table_id, data)
            results[key] = {"rows": row_count, "table": table_id}
        except Exception as e:
            results[key] = {"error": str(e)}

    return (json.dumps(results), 200)


def health_check(request):
    return ("OK", 200)
