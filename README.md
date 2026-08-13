# cfb-grid-data

Ingests college football data from the [CollegeFootballData API](https://collegefootballdata.com/) and lands it in BigQuery for later transformation.

## Architecture

```
Cloud Scheduler --> Cloud Run (app.py / bq_ingest.py)
                       |
                       |-- 1. fetch each CFBD endpoint
                       |-- 2. write raw rows as NDJSON to GCS (landing zone)
                       |          gs://<bucket>/raw/<endpoint>/year=<Y>/week=<W>/<run-ts>.json
                       `-- 3. load that GCS file into BigQuery raw_<endpoint> tables (append)
```

Each ingest run fetches every configured endpoint, writes an immutable NDJSON snapshot to Cloud Storage first, then BigQuery-loads that GCS object into the corresponding `raw_<endpoint>` table with `WRITE_APPEND`. Because the GCS snapshot is kept, a bad API response or a downstream BigQuery schema issue can be replayed/reloaded without re-hitting the CFBD API. Each row gets ingestion metadata columns (`_ingested_at`, `_source_endpoint`, `_year`, `_week`) so it can be traced back to the run and source file that produced it.

Raw tables are intentionally untransformed — build views/scheduled queries/dbt models on top of them for anything analysis-ready.

## Structure

- `app.py` - Flask app (served by gunicorn) exposing `/health` and `/ingest` (`/` is an alias).
- `bq_ingest.py` - fetches CFBD endpoints, lands NDJSON in GCS, loads into BigQuery.
- `Dockerfile` - container image used for the Cloud Run deploy.
- `requirements.txt` - Python dependencies.
- `.gcloudignore` / `.dockerignore` - files excluded from gcloud/Docker builds.
- `.github/workflows/deploy.yml` - deploys the container to Cloud Run on push to `main`.

## Endpoints ingested

| key | CFBD endpoint | BigQuery table |
| --- | --- | --- |
| `games` | `games` (year + week) | `raw_games` |
| `records` | `records` (year) | `raw_records` |
| `rankings` | `rankings` (year + week) | `raw_rankings` |
| `venues` | `venues` | `raw_venues` |
| `all_games` | `games` (year only) | `raw_all_games` |
| `win_prob` | `metrics/wp/pregame` (year + week) | `raw_win_prob` |

Add new endpoints by adding an entry to the `ENDPOINTS` dict in `bq_ingest.py`.

## Usage

HTTP GET/POST with JSON body or query params:

```bash
curl -X POST https://YOUR-CLOUD-RUN-URL/ingest \
  -H "Content-Type: application/json" \
  -d '{"year":2025,"week":1}'
```

Params:
- `year` - defaults to `DEFAULT_YEAR` env var (2025)
- `week` - defaults to `DEFAULT_WEEK` env var (1)
- `dataset` - BigQuery dataset name (falls back to `BQ_DATASET` env var)
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

Deployment builds the Dockerfile and deploys it to Cloud Run via `.github/workflows/deploy.yml`, using Workload Identity Federation (OIDC) — no service account key file is stored in GitHub.

Manual deploy (equivalent to what CI does):

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

```bash
gcloud scheduler jobs create http cfb-grid-weekly-ingest \
  --location YOUR_REGION \
  --schedule "0 8 * * 2" \
  --uri "https://YOUR-CLOUD-RUN-URL/ingest" \
  --http-method POST \
  --message-body '{"year":2025,"week":1}'
```

Update the `week` in the message body (or drop it and manage `DEFAULT_WEEK` per season) as the season progresses.

### GitHub Actions CI/CD

`.github/workflows/deploy.yml` builds and deploys the container to Cloud Run on push to `main`. Required repository secrets:

- `WORKLOAD_IDENTITY_PROVIDER` - full resource name of the Workload Identity Federation provider.
- `GCP_SA_EMAIL` - service account email the workflow impersonates via OIDC.
- `GCP_PROJECT` - Google Cloud project ID.
- `GCP_REGION` - Cloud Run region.
- `GCS_BUCKET` - GCS landing bucket.
- `BQ_DATASET` - BigQuery dataset name.

See `SECRET_SAMPLE.md` for the full list and setup notes. The workflow never handles the CFBD API key directly — it's injected into the running service from Secret Manager.
