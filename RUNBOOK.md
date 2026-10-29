# Runbook: manually triggering ingest / transform

Quick reference for kicking off a run against the **live** production services, without waiting for the daily Cloud Scheduler jobs. See `README.md` for full param docs and `CLAUDE.md` for the non-obvious gotchas behind these design choices.

## Live resource names

- Ingest Cloud Run URL: `https://cfb-grid-ingest-cwzkr574pa-uc.a.run.app` (public, no auth needed)
- Transform Cloud Run URL: `https://cfb-grid-transform-cwzkr574pa-uc.a.run.app` (private, needs an identity token)
- Region: `us-central1`
- Scheduler jobs (location `us-central1`):
  - `cfb-grid-daily-ingest` — `year=2025, week=all` against `/ingest`
  - `cfb-grid-daily-transform-week-01` through `-15` — one `/transform` call per week

## Option 1: re-run an existing Cloud Scheduler job

Easiest option if the default params (current year, that job's week) are what you want — no params to type, just re-fires the job's saved payload.

```bash
gcloud scheduler jobs run cfb-grid-daily-ingest --location us-central1

gcloud scheduler jobs run cfb-grid-daily-transform-week-01 --location us-central1
```

## Option 2: curl the service directly (custom year/week)

Use this when you need a specific year/week the scheduled jobs don't cover, or want to point at a scratch `dataset`/`collection` for testing (see [Testing](#testing) below).

```bash
# ingest — public, no auth header needed
curl -X POST https://cfb-grid-ingest-cwzkr574pa-uc.a.run.app/ingest \
  -H "Content-Type: application/json" \
  -d '{"year":2025,"week":1}'

# transform — private, needs a GCP identity token from an account with invoker access
curl -X POST https://cfb-grid-transform-cwzkr574pa-uc.a.run.app/transform \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"year":2025,"week":1}'
```

`week=all` works on both endpoints for one-off backfills, but a `week=all` **transform** request reliably has its connection dropped after a few minutes even though the run completes successfully server-side — that's why production uses 15 separate per-week scheduler jobs instead of one `week=all` job. Safe to use manually if you don't mind the (harmless) dropped connection and don't need to see the response.

## Testing

Never point a manual run at production data without meaning to. Add these params to test first:

- Ingest: `"dataset": "your_scratch_dataset"` (and/or `"bucket": "your-scratch-bucket"`)
- Transform: `"dataset": "your_scratch_dataset", "collection": "games_test"`

## College basketball (`cbb/`)

Both CBB services are private (identity token required). Scheduler jobs: `cfb-grid-cbb-daily-ingest`, `cfb-grid-cbb-daily-transform`, `cfb-grid-cbb-weekly-ingest`, `cfb-grid-cbb-weekly-transform` — rerun any with `gcloud scheduler jobs run <name> --location us-central1`. Ad hoc:

```bash
TOKEN=$(gcloud auth print-identity-token)
INGEST=$(gcloud run services describe cfb-grid-cbb-ingest --region us-central1 --format='value(status.url)')
TRANSFORM=$(gcloud run services describe cfb-grid-cbb-transform --region us-central1 --format='value(status.url)')

# a date range (Eastern dates), or {"range":"season"} for the whole season
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"start":"2026-11-01","end":"2026-11-30"}' "$INGEST/ingest"
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"start":"2026-11-01","end":"2026-11-30"}' "$TRANSFORM/transform"
```

The deployed transform writes to `cbb-games` (`MONGO_COLLECTION`); pass `"collection":"cbb-games_test"` to test without touching production. See `cbb/README.md` for params and gotchas.
