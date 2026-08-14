import os
import json

from flask import Flask, Response, request
from google.cloud import bigquery

import bq_reader
import bq_transform
import mongo_writer

app = Flask(__name__)

_colors_cache = None


def _colors():
    global _colors_cache
    if _colors_cache is None:
        _colors_cache = bq_transform.load_colors()
    return _colors_cache


def _param(request_args, request_json, name, default=None):
    return request_args.get(name) or request_json.get(name) or default


def transform_and_load(request):
    """HTTP entrypoint -- read the raw_* BigQuery tables for a year/week,
    reproduce the legacy grid transform, and write per-timezone game
    documents into MongoDB's `cfb-grid.<collection>` collection (delete+insert,
    matching legacy overwrite semantics).

    Query params / JSON body:
      - year (int)
      - week (int)
      - dataset (BigQuery dataset, defaults to BQ_DATASET env var)
      - collection (Mongo collection name, defaults to MONGO_COLLECTION env var or 'games' --
        override to e.g. 'games_test' to verify without touching production data)

    Environment:
      - MONGODB_URI (Secret Manager)
      - BQ_DATASET / BQ_PROJECT (optional)
      - MONGO_COLLECTION (optional, defaults to 'games')
    """
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    year = int(_param(request_args, request_json, "year", os.environ.get("DEFAULT_YEAR", 2025)))
    week = int(_param(request_args, request_json, "week", os.environ.get("DEFAULT_WEEK", 1)))
    dataset = _param(request_args, request_json, "dataset", os.environ.get("BQ_DATASET"))
    collection = _param(request_args, request_json, "collection", os.environ.get("MONGO_COLLECTION", "games"))

    if not dataset:
        return ("Missing BigQuery dataset. Provide 'dataset' param or set BQ_DATASET env var.", 400)

    mongodb_uri = os.environ.get("MONGODB_URI")
    if not mongodb_uri:
        return ("Missing MONGODB_URI environment variable", 500)

    bq_client = bigquery.Client(project=os.environ.get("BQ_PROJECT"))
    project = bq_client.project
    table = lambda key: f"{project}.{dataset}.raw_{key}"

    try:
        games_df = bq_reader.read_games(bq_client, table("games"), year, week)
        media_df = bq_reader.read_media(bq_client, table("media"), year, week)
        tv_df = bq_reader.read_tv_schedule(bq_client, table("tv_schedule"), year, week)
        venues_df = bq_reader.read_venues(bq_client, table("venues"))
        rankings_df = bq_reader.read_playoff_committee_rankings(bq_client, table("rankings"), year, week)
        records_df = bq_reader.read_records(bq_client, table("records"), year)
        win_prob_df = bq_reader.read_win_prob(bq_client, table("win_prob"), year, week)
        all_games = bq_reader.read_all_games(bq_client, table("all_games"), year)
    except Exception as e:
        return (f"BigQuery read failed: {e}", 502)

    try:
        merged_df, tv_unmatched_rows = bq_transform.build_merged_dataframe(
            games_df, media_df, tv_df, venues_df, rankings_df, records_df, win_prob_df, _colors()
        )
        games_by_timezone = bq_transform.generate_all_timezones(merged_df, all_games)
    except Exception as e:
        return (f"Transform failed: {e}", 500)

    results = {"tv_schedule_unmatched": len(tv_unmatched_rows)}
    for timezone, game_list in games_by_timezone.items():
        try:
            _, deleted_count = mongo_writer.write_to_mongodb(
                mongodb_uri, "cfb-grid", collection, game_list, year, week, timezone, overwrite=True
            )
            results[timezone] = {"games_written": len(game_list), "deleted_count": deleted_count}
        except Exception as e:
            results[timezone] = {"error": f"mongo write failed: {e}"}

    return (json.dumps(results), 200)


def health_check(request):
    return ("OK", 200)


@app.route("/health", methods=["GET"])
def health_route():
    body, status = health_check(request)
    return Response(str(body), status=status, mimetype="text/plain")


@app.route("/", methods=["GET", "POST"])
@app.route("/transform", methods=["GET", "POST"])
def transform_route():
    result = transform_and_load(request)

    if isinstance(result, tuple):
        body, status = result
        if isinstance(body, str):
            return Response(body, status=status, mimetype="application/json")
        return body, status

    return result


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
