import os
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, Response, request
from google.cloud import bigquery

import bq_reader
import cbb_transform
import mongo_writer

app = Flask(__name__)

# Same defaults as the CBB ingest's rolling window.
DEFAULT_WINDOW_DAYS_BACK = 1
DEFAULT_WINDOW_DAYS_AHEAD = 21


def _param(request_args, request_json, name, default=None):
    value = request_args.get(name)
    if value is None:
        value = request_json.get(name)
    return default if value is None else value


def season_bounds(season: int):
    """Matches cbb/ingest/bq_ingest.py::season_bounds."""
    return date(season - 1, 11, 1), date(season, 4, 15)


def transform_and_load(request):
    """HTTP entrypoint -- read the CBB raw_* BigQuery tables for an Eastern
    date range and rewrite those dates' per-timezone game documents in
    MongoDB's `cfb-grid.<collection>` collection.

    Query params / JSON body:
      - season (int, CBBD numbering -- 2027 for 2026-27)
      - start / end (YYYY-MM-DD Eastern slate dates; default yesterday .. +21 days)
      - range ('season' for the whole season -- prefer monthly start/end
        chunks on a schedule; see the football week=all connection-drop gotcha)
      - dataset (BigQuery dataset, defaults to BQ_DATASET env var)
      - collection (Mongo collection, defaults to MONGO_COLLECTION env var or
        'cbb-games_test' -- production must be opted into explicitly)

    Environment:
      - MONGODB_URI (Secret Manager)
      - BQ_DATASET / BQ_PROJECT (optional)
      - MONGO_COLLECTION / DEFAULT_SEASON (optional)
    """
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    season = int(_param(request_args, request_json, "season", os.environ.get("DEFAULT_SEASON", 2027)))
    dataset = _param(request_args, request_json, "dataset", os.environ.get("BQ_DATASET"))
    collection = _param(request_args, request_json, "collection", os.environ.get("MONGO_COLLECTION", "cbb-games_test"))

    if str(_param(request_args, request_json, "range", "")).strip().lower() == "season":
        start, end = season_bounds(season)
    else:
        today = datetime.now(ZoneInfo("America/New_York")).date()
        try:
            start = date.fromisoformat(_param(request_args, request_json, "start", (today - timedelta(days=DEFAULT_WINDOW_DAYS_BACK)).isoformat()))
            end = date.fromisoformat(_param(request_args, request_json, "end", (today + timedelta(days=DEFAULT_WINDOW_DAYS_AHEAD)).isoformat()))
        except ValueError as e:
            return (f"Invalid start/end date: {e}", 400)
        if end < start:
            return ("'end' must be on or after 'start'", 400)

    if not dataset:
        return ("Missing BigQuery dataset. Provide 'dataset' param or set BQ_DATASET env var.", 400)

    mongodb_uri = os.environ.get("MONGODB_URI")
    if not mongodb_uri:
        return ("Missing MONGODB_URI environment variable", 500)

    bq_client = bigquery.Client(project=os.environ.get("BQ_PROJECT"))
    table = lambda key: f"{bq_client.project}.{dataset}.raw_{key}"

    try:
        games = bq_reader.read_games(bq_client, table("games"), season, start, end)
        media = bq_reader.read_media(bq_client, table("media"), season, start, end)
        teams = bq_reader.read_teams(bq_client, table("teams"), season)
        rankings = bq_reader.read_rankings(bq_client, table("rankings"), season)
        final_games = bq_reader.read_final_games(bq_client, table("games"), season)
    except Exception as e:
        return (f"BigQuery read failed: {e}", 502)

    if not teams:
        return (f"No raw_teams rows for season {season} -- run the ingest with range=season (or teams=1) first.", 409)

    try:
        docs_by_date, stats = cbb_transform.build_documents(games, media, teams, rankings, final_games)
    except Exception as e:
        return (f"Transform failed: {e}", 500)

    try:
        written = mongo_writer.write_date_range(mongodb_uri, "cfb-grid", collection, season, start, end, docs_by_date)
    except Exception as e:
        return (f"Mongo write failed: {e}", 502)

    return (json.dumps({
        "season": season,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "collection": collection,
        "games_read": len(games),
        **stats,
        "dates": written,
    }), 200)


@app.route("/health", methods=["GET"])
def health_route():
    return Response("OK", status=200, mimetype="text/plain")


@app.route("/", methods=["GET", "POST"])
@app.route("/transform", methods=["GET", "POST"])
def transform_route():
    body, status = transform_and_load(request)
    return Response(body, status=status, mimetype="application/json" if status == 200 else "text/plain")


@app.route("/ensure-indexes", methods=["POST"])
def ensure_indexes_route():
    """Idempotent index creation over HTTP; optional `collection` param."""
    mongodb_uri = os.environ.get("MONGODB_URI")
    if not mongodb_uri:
        return Response("Missing MONGODB_URI environment variable", status=500, mimetype="text/plain")

    request_json = request.get_json(silent=True) or {}
    collection = request_json.get("collection") or request.args.get("collection") or "cbb-games"
    try:
        mongo_writer.ensure_indexes(mongodb_uri, collection_name=collection)
    except Exception as e:
        return Response(f"Index creation failed: {e}", status=500, mimetype="text/plain")
    return Response(f"Indexes ensured on '{collection}' collection.", status=200, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
