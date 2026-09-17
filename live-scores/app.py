import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, Response, request
from pymongo import MongoClient
import certifi
import requests

import current_week
import mongo_patcher
import ncaa_client
import team_match

app = Flask(__name__)

EASTERN = ZoneInfo("America/New_York")


def _param(request_args, request_json, name, default=None):
    # 'or' chaining would treat week=0 (a real CFB week) as "not provided"
    # and silently fall through to the default -- check for None explicitly.
    # Same trap transform/app.py's _param() guards against.
    value = request_args.get(name)
    if value is None:
        value = request_json.get(name)
    return default if value is None else value


def _poll_one_week(collection, ncaa_session, season: int, week: int) -> dict:
    ncaa_games, fetch_errors = ncaa_client.fetch_all_divisions(season, week, session=ncaa_session)
    if not ncaa_games and fetch_errors:
        return {"error": f"NCAA fetch failed for all divisions: {fetch_errors}"}

    actionable = [g for g in ncaa_games if g.get("gameState") != "pre"]

    game_lookup = team_match.build_game_lookup(collection, season)
    matched, unmatched = team_match.match_ncaa_games(actionable, game_lookup)

    try:
        patch_result = mongo_patcher.patch_live_games(collection, season, matched)
    except Exception as e:
        return {"error": f"Mongo patch failed: {e}"}

    mongo_names = {name for pair in game_lookup for name in pair}
    unmatched_detail = []
    for g in unmatched:
        away_short = g.get("away", {}).get("names", {}).get("short")
        home_short = g.get("home", {}).get("names", {}).get("short")
        away_resolved = team_match._resolve_mongo_name(away_short, mongo_names)
        home_resolved = team_match._resolve_mongo_name(home_short, mongo_names)
        actual_opponents = [
            pair for pair in game_lookup
            if (away_resolved and away_resolved in pair) or (home_resolved and home_resolved in pair)
        ]
        unmatched_detail.append({
            "title": g.get("title"),
            "away_short": away_short,
            "home_short": home_short,
            "away_resolved": away_resolved,
            "home_resolved": home_resolved,
            "mongo_pairs_involving_either_side": actual_opponents,
        })

    return {
        "fetched": len(ncaa_games),
        "actionable": len(actionable),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "unmatched_detail": unmatched_detail,
        "mongo_docs_for_slot": len(game_lookup),
        "patch_result": patch_result,
        "fetch_errors": fetch_errors,
    }


def poll_live_scores(request):
    """HTTP entrypoint -- determine the current (season, week) slot(s) from
    Mongo (or an explicit override), fetch the NCAA scoreboard for each, and
    patch live_* fields onto matched game documents.

    Query params / JSON body:
      - season (int, optional) / week (int, optional) -- explicit override,
        bypassing current-week auto-detection. Provide both together (used
        for testing against a known past week, e.g. season=2025&week=3).
      - collection (Mongo collection name, defaults to MONGO_COLLECTION env
        var or 'games_test' -- deliberately test-safe by default, unlike
        transform's default of 'games'; override to 'games' only once
        verified)

    Environment:
      - MONGODB_URI (Secret Manager)
      - MONGO_COLLECTION (optional, defaults to 'games_test')
    """
    request_json = request.get_json(silent=True) or {}
    request_args = request.args or {}

    collection_name = _param(
        request_args, request_json, "collection", os.environ.get("MONGO_COLLECTION", "games_test")
    )
    season_override = _param(request_args, request_json, "season")
    week_override = _param(request_args, request_json, "week")

    mongodb_uri = os.environ.get("MONGODB_URI")
    if not mongodb_uri:
        return ("Missing MONGODB_URI environment variable", 500)

    client = MongoClient(
        mongodb_uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=30000,
        connectTimeoutMS=30000,
        socketTimeoutMS=30000,
    )
    try:
        collection = client["cfb-grid"][collection_name]

        if season_override is not None and week_override is not None:
            weeks = [(int(season_override), int(week_override))]
        else:
            today = datetime.now(EASTERN).date()
            weeks = current_week.current_weeks(collection, today)

        if not weeks:
            return (json.dumps({"weeks_polled": [], "results": {}}), 200)

        ncaa_session = requests.Session()
        results = {}
        for season, week in weeks:
            results[f"{season}-{week}"] = _poll_one_week(collection, ncaa_session, season, week)

        return (json.dumps({"weeks_polled": weeks, "results": results}), 200)
    finally:
        client.close()


def health_check(request):
    return ("OK", 200)


@app.route("/health", methods=["GET"])
def health_route():
    body, status = health_check(request)
    return Response(str(body), status=status, mimetype="text/plain")


@app.route("/", methods=["GET", "POST"])
@app.route("/poll", methods=["GET", "POST"])
def poll_route():
    body, status = poll_live_scores(request)
    return Response(body, status=status, mimetype="application/json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
