# cfb-grid-data

Two-stage data pipeline for [cfbgrid.com](https://cfbgrid.com): ingests college football data from the [CollegeFootballData API](https://collegefootballdata.com/) plus a supplemental TV-schedule scrape, lands it in BigQuery, then transforms and writes the final per-game documents to MongoDB.

## Architecture

```
Cloud Scheduler --> Cloud Run: ingest (app.py / bq_ingest.py)
                       |
                       |-- 1. fetch each CFBD endpoint + scrape fbschedules.com
                       |-- 2. write raw rows as NDJSON to GCS (landing zone)
                       |          gs://<bucket>/raw/<endpoint>/year=<Y>/week=<W>/<run-ts>.json
                       `-- 3. load that GCS file into BigQuery raw_<endpoint> tables (append)

Cloud Scheduler --> Cloud Run: transform (transform/app.py)
                       |
                       |-- 1. read raw_* BigQuery tables (deduped by _ingested_at)
                       |-- 2. merge/enrich (outlets, rankings, records, colors, timezones)
                       `-- 3. write final game documents to MongoDB `cfb-grid.games`
```

Each ingest run fetches every configured endpoint, writes an immutable NDJSON snapshot to Cloud Storage first, then BigQuery-loads that GCS object into the corresponding `raw_<endpoint>` table with `WRITE_APPEND`. Because the GCS snapshot is kept, a bad API response or a downstream BigQuery schema issue can be replayed/reloaded without re-hitting the CFBD API. Each row gets ingestion metadata columns (`_ingested_at`, `_source_endpoint`, `_year`, `_week`) so it can be traced back to the run and source file that produced it.

Raw tables are intentionally untransformed — the `transform/` service (see [below](#transform-service)) is what turns them into the documents MongoDB/[`cfb-grid-server`](https://github.com/rbharrison1/cfb-grid) actually serve.

## Structure

- `app.py` - Flask app (served by gunicorn) exposing `/health` and `/ingest` (`/` is an alias).
- `bq_ingest.py` - fetches CFBD endpoints + the fbschedules.com scrape, lands NDJSON in GCS, loads into BigQuery.
- `fbschedules.py` - scraper for fbschedules.com's TV schedule (see the `tv_schedule` row below).
- `Dockerfile` - container image used for the ingest Cloud Run deploy.
- `requirements.txt` - Python dependencies.
- `.gcloudignore` / `.dockerignore` - files excluded from gcloud/Docker builds.
- `.github/workflows/deploy.yml` - **currently non-functional** (see [Deployment](#deployment)); intended to deploy the ingest container to Cloud Run on push to `main`.
- `transform/` - the second stage; its own Cloud Run service, README-documented [below](#transform-service).

## Endpoints ingested

| key | CFBD endpoint | BigQuery table |
| --- | --- | --- |
| `games` | `games` (year + week) | `raw_games` |
| `records` | `records` (year) | `raw_records` |
| `rankings` | `rankings` (year + week) | `raw_rankings` |
| `venues` | `venues` | `raw_venues` |
| `all_games` | `games` (year only) | `raw_all_games` |
| `win_prob` | `metrics/wp/pregame` (year + week) | `raw_win_prob` |
| `tv_schedule` | scraped from [fbschedules.com](https://fbschedules.com/college-football-tv-schedule/) (year + week) | `raw_tv_schedule` |

Add new CFBD endpoints by adding an entry to the `ENDPOINTS` dict in `bq_ingest.py`.

`tv_schedule` is not a CFBD endpoint — it's scraped from fbschedules.com's TV schedule page, since CFBD's `games/media` endpoint doesn't cover every game (notably Group of 5 / FCS games on streaming-only outlets) and CFBD's own `startDate` is sometimes wrong or a startTimeTBD placeholder. fbschedules.com is the transform's source of truth for outlet, date, and time — CFBD's own values are only a fallback for whichever games/fields fbschedules didn't match or didn't have a usable value for (see `tv_schedule_match.py` / `bq_transform.py::_apply_fbschedules_schedule`). See `fbschedules.py`. It's landed the same way as every other raw table but fetched/parsed separately in `bq_ingest()`, and never fails the rest of the ingest run if the scrape breaks.

Note: CFBD's own `week` numbering has no separate "week 0" — it folds season-opening games into `week=1`. fbschedules.com numbers them separately (its own "Week 0" and "Week 1"). The ingest/transform code reconciles this (see `fbschedules.py`'s `_fbschedules_weeks_for_cfbd_week`), so `raw_tv_schedule` rows for CFBD `week=1` may span both of fbschedules' Week 0 and Week 1; every week ≥2 maps 1:1.

## Usage

HTTP GET/POST with JSON body or query params:

```bash
curl -X POST https://YOUR-CLOUD-RUN-URL/ingest \
  -H "Content-Type: application/json" \
  -d '{"year":2025,"week":1}'
```

Params:
- `year` - defaults to `DEFAULT_YEAR` env var (2025)
- `week` - defaults to `DEFAULT_WEEK` env var (1); pass `week=all` to ingest every week of the season in one run (used by the daily scheduled job, see [Scheduling runs](#scheduling-runs-with-cloud-scheduler))
- `dataset` - BigQuery dataset name (falls back to `BQ_DATASET` env var) — use a scratch dataset here to test without touching production tables
- `bucket` - GCS landing bucket (falls back to `GCS_BUCKET` env var)

## Environment variables

- `CFBD_API_KEY` - CollegeFootballData API key (inject via Secret Manager).
- `GCS_BUCKET` - GCS bucket used as the raw landing zone.
- `BQ_DATASET` - BigQuery dataset that holds the `raw_*` tables (created if missing).
- `BQ_PROJECT` - optional, defaults to the ambient Cloud Run project.
- `BQ_LOCATION` - optional, defaults to `US` (used only when creating the dataset).
- `DEFAULT_YEAR` / `DEFAULT_WEEK` - defaults when not passed as params.

## Local development

```bash
pip install -r requirements.txt
export CFBD_API_KEY=... GCS_BUCKET=... BQ_DATASET=...
python app.py
```

## Deployment

**Deploys are currently manual.** `.github/workflows/deploy.yml` is set up to build and deploy via Workload Identity Federation (OIDC) on push to `main`, but its OIDC auth is currently broken and the workflow does not successfully deploy — don't rely on a push to `main` reaching production. Until that's fixed, deploy by hand from the repo root (ingest) and from `transform/` (transform service, see [below](#transform-service)):

```bash
gcloud run deploy cfb-grid-ingest \
  --source . \
  --region YOUR_REGION \
  --allow-unauthenticated \
  --set-env-vars GCS_BUCKET=YOUR_BUCKET,BQ_DATASET=YOUR_DATASET,DEFAULT_YEAR=2025,DEFAULT_WEEK=1 \
  --set-secrets CFBD_API_KEY=CFBD_API_KEY:latest
```

### Secret Manager for `CFBD_API_KEY`

```bash
printf "%s" "YOUR_API_KEY" | gcloud secrets create CFBD_API_KEY --data-file=- --replication-policy="automatic"
# or, to rotate:
printf "%s" "YOUR_API_KEY" | gcloud secrets versions add CFBD_API_KEY --data-file=-

gcloud secrets add-iam-policy-binding CFBD_API_KEY \
  --member="serviceAccount:YOUR_CLOUD_RUN_SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor"
```

### IAM for the Cloud Run service account

The service account Cloud Run runs as needs:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_CLOUD_RUN_SERVICE_ACCOUNT" \
  --role="roles/storage.objectAdmin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_CLOUD_RUN_SERVICE_ACCOUNT" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_CLOUD_RUN_SERVICE_ACCOUNT" \
  --role="roles/bigquery.jobUser"
```

Dataset creation requires broader permissions (e.g. `roles/bigquery.dataOwner` on the project, or pre-create the dataset yourself and skip the `dataEditor` grant above in favor of a dataset-scoped role).

### Scheduling runs with Cloud Scheduler

Production runs two Cloud Scheduler jobs against these two services:

- `cfb-grid-daily-ingest` — once daily, `week=all`, against `/ingest` on the ingest service.
- `cfb-grid-daily-transform-week-01` through `-15` — once daily, staggered a couple minutes apart, one `/transform` call per week against the transform service (see [Transform service](#transform-service) for why it's split into 15 jobs instead of one `week=all` job).

Example job creation:

```bash
gcloud scheduler jobs create http cfb-grid-daily-ingest \
  --location YOUR_REGION \
  --schedule "0 5 * * *" \
  --time-zone "America/New_York" \
  --uri "https://YOUR-INGEST-CLOUD-RUN-URL/ingest" \
  --http-method POST \
  --message-body '{"year":2025,"week":"all"}'
```

### GitHub Actions CI/CD

`.github/workflows/deploy.yml` is intended to build and deploy the ingest container to Cloud Run on push to `main`, but its OIDC auth is currently broken (see [Deployment](#deployment)) — it does not currently deploy anything. If/when it's fixed, it expects these repository secrets:

- `WORKLOAD_IDENTITY_PROVIDER` - full resource name of the Workload Identity Federation provider.
- `GCP_SA_EMAIL` - service account email the workflow impersonates via OIDC.
- `GCP_PROJECT` - Google Cloud project ID.
- `GCP_REGION` - Cloud Run region.
- `GCS_BUCKET` - GCS landing bucket.
- `BQ_DATASET` - BigQuery dataset name.

See `SECRET_SAMPLE.md` for the full list and setup notes. The workflow never handles the CFBD API key directly — it's injected into the running service from Secret Manager.

## Transform service

The second stage, in `transform/` — its own Cloud Run service (`cfb-grid-transform`), deployed separately from the ingest service above and kept private (not public like ingest).

Reads the `raw_*` BigQuery tables ingest lands, merges/enriches them (outlet/date/time from fbschedules.com where available, falling back to CFBD; AP Top 25 rankings, team records, venues, colors, per-timezone formatting and grid-column placement), and writes the final game documents into MongoDB (`cfb-grid.<collection>`, default collection `games`).

### Structure

- `app.py` - Flask app exposing `/health` and `/transform` (`/` is an alias).
- `bq_reader.py` - reads/dedupes the `raw_*` BigQuery tables.
- `bq_transform.py` - merge/format logic; ported from `cfb-grid-python`'s `generate_sched.py`.
- `tv_schedule_match.py` - matches fbschedules.com rows to CFBD games by team name, surfacing fbschedules' outlet/date/time as overlay dicts keyed by CFBD game id (CFBD's own data is the fallback, applied in `bq_transform.py`).
- `mongo_writer.py` - writes final game documents to MongoDB (delete-then-insert per `(season, week, timezone)` on overwrite).
- `config/networks.py`, `config/teams.py` - network/team display-name and grid-column mapping tables.
- `colors.csv` - team color lookup, bundled into the image.

### Usage

```bash
curl -X POST https://YOUR-TRANSFORM-CLOUD-RUN-URL/transform \
  -H "Content-Type: application/json" \
  -d '{"year":2025,"week":1}'
```

Params:
- `year` - defaults to `DEFAULT_YEAR` env var
- `week` - defaults to `DEFAULT_WEEK` env var; pass `week=all` to process every week already landed in `raw_games` for that year in one request (see the connection-drop note below before scheduling this on a recurring basis)
- `dataset` - BigQuery dataset to read from (falls back to `BQ_DATASET` env var)
- `collection` - MongoDB collection to write to (falls back to `MONGO_COLLECTION` env var, default `games`) — **use `games_test` to verify changes without touching production data**

### Environment variables

- `MONGODB_URI` - MongoDB connection string (inject via Secret Manager).
- `BQ_DATASET` / `BQ_PROJECT` - same meaning as the ingest service.
- `MONGO_COLLECTION` - optional, defaults to `games`.
- `DEFAULT_YEAR` / `DEFAULT_WEEK` - defaults when not passed as params.

### A note on `week=all`

A single `/transform` request with `week=all` reliably has its connection dropped by intermediate infrastructure after a few minutes of silence, even though the work completes successfully server-side (Flask/gunicorn buffer the entire response and emit nothing until it's done). Because of this, the daily scheduled job does **not** call `/transform?week=all` — it's split into 15 separate per-week Cloud Scheduler jobs instead (see [Scheduling runs](#scheduling-runs-with-cloud-scheduler)). `week=all` is still useful for one-off manual backfills where you can tolerate a long-hanging client request, or for local/scripted use where you don't mind the (harmless) dropped connection.
