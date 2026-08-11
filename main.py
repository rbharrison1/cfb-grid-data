import os
import json
import requests

from google.cloud import storage

CFBD_API_BASE = "https://api.collegefootballdata.com"


def fetch_cfbd_data(year: int, week: int, endpoint: str) -> dict:
    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        raise EnvironmentError("CFBD_API_KEY is required")

    url = f"{CFBD_API_BASE}/{endpoint}"
    params = {"year": year, "week": week}
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def write_json_to_gcs(bucket_name: str, destination_path: str, data: dict) -> str:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_path)
    blob.upload_from_string(json.dumps(data), content_type="application/json")
    return f"gs://{bucket_name}/{destination_path}"


def ingest_game_data(request):
    """HTTP Cloud Function to fetch College Football data and upload to GCS."""
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    year = int(request_args.get("year") or request_json.get("year") or os.environ.get("DEFAULT_YEAR", 2025))
    week = int(request_args.get("week") or request_json.get("week") or os.environ.get("DEFAULT_WEEK", 1))
    bucket_name = request_args.get("bucket") or request_json.get("bucket") or os.environ.get("GCS_BUCKET")

    if not bucket_name:
        return ("Missing GCS_BUCKET environment variable or bucket parameter", 400)

    data = {
        "games": fetch_cfbd_data(year, week, "games"),
        "records": fetch_cfbd_data(year, week, "team/records"),
        "polls": fetch_cfbd_data(year, week, "rankings"),
    }

    destination = f"cfb-grid-data/{year}/week-{week}/ingest.json"
    gcs_uri = write_json_to_gcs(bucket_name, destination, data)

    return {
        "status": "success",
        "year": year,
        "week": week,
        "gcs_uri": gcs_uri,
    }


def health_check(request):
    """Simple HTTP health check for Cloud Functions."""
    return ("OK", 200)
