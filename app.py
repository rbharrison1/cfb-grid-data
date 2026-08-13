import os

from flask import Flask, Response, request

from bq_ingest import bq_ingest, health_check

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health_route():
    body, status = health_check(request)
    return Response(str(body), status=status, mimetype="text/plain")


@app.route("/", methods=["GET", "POST"]) @app.route("/ingest", methods=["GET", "POST"])
def ingest_route():
    result = bq_ingest(request)

    if isinstance(result, tuple):
        body, status = result
        if isinstance(body, str):
            return Response(body, status=status, mimetype="application/json")
        return body, status

    return result


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
