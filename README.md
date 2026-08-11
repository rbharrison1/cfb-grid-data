# cfb-grid-data

This repository contains Python scripts for ingesting college football data and exporting it to Google Cloud Storage via Cloud Functions.

## Structure

- `main.py` - Cloud Functions entrypoint with ingest and health-check handlers.
- `requirements.txt` - Python dependencies.
- `.gcloudignore` - files excluded from gcloud deploy.

## Deployment

1. Set Cloud project:
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   ```
2. Deploy the Cloud Function:
   ```bash
   gcloud functions deploy ingest_game_data \
     --runtime python310 \
     --trigger-http \
     --entry-point ingest_game_data \
     --allow-unauthenticated \
     --region YOUR_REGION \
     --set-env-vars GCS_BUCKET=YOUR_BUCKET,DEFAULT_YEAR=2025,DEFAULT_WEEK=1
   ```

### Using Secret Manager for `CFBD_API_KEY`

Create a secret in Secret Manager and add your API key securely:

```bash
printf "%s" "YOUR_API_KEY" | gcloud secrets create CFBD_API_KEY --data-file=- --replication-policy="automatic"
```

If the secret already exists, add a new version instead:

```bash
printf "%s" "YOUR_API_KEY" | gcloud secrets versions add CFBD_API_KEY --data-file=-
```

Grant the Cloud Functions runtime service account access to read the secret. Replace `FUNCTION_SA` with the service account used by your function (for default Cloud Functions service accounts, this is typically `PROJECT_NUMBER-compute@developer.gserviceaccount.com` or `PROJECT_ID@appspot.gserviceaccount.com`):

```bash
gcloud secrets add-iam-policy-binding CFBD_API_KEY \
  --member="serviceAccount:FUNCTION_SA" \
  --role="roles/secretmanager.secretAccessor"
```

Then deploy with the secret bound to the `CFBD_API_KEY` environment variable:

```bash
gcloud functions deploy ingest_game_data \
  --runtime python310 \
  --trigger-http \
  --entry-point ingest_game_data \
  --allow-unauthenticated \
  --region YOUR_REGION \
  --set-env-vars GCS_BUCKET=YOUR_BUCKET,DEFAULT_YEAR=2025,DEFAULT_WEEK=1 \
  --set-secrets CFBD_API_KEY=CFBD_API_KEY:latest
```

The function code reads the API key from the `CFBD_API_KEY` environment variable.

## GitHub Secrets for CI/CD

In your GitHub repository settings, add these secrets so the workflow can deploy securely:

- `GCP_SA_KEY`: JSON service account key for Cloud Function deployment.
- `GCP_PROJECT`: Google Cloud project ID.
- `GCP_REGION`: Cloud Functions region.
- `GCS_BUCKET`: Storage bucket used by the function.

The workflow does not store your API key directly in GitHub; it uses Secret Manager to inject `CFBD_API_KEY` at deployment time.

## Usage

- HTTP POST/GET with JSON body or query params:
  - `year`
  - `week`
  - `bucket`

Example:
```bash
curl -X POST https://REGION-PROJECT_ID.cloudfunctions.net/ingest_game_data \
  -H "Content-Type: application/json" \
  -d '{"year":2025,"week":1}'
```

## Environment Variables

- `CFBD_API_KEY` - CollegeFootballData API key.
- `GCS_BUCKET` - Default Cloud Storage bucket for ingest output.
- `DEFAULT_YEAR` - default year when none is provided.
- `DEFAULT_WEEK` - default week when none is provided.

## Notes

- This repo is intentionally minimal so it can be used as a Cloud Functions deployment source.
- Add new ingestion helpers to `main.py` or split them into packages as needed.

## GitHub Actions CI/CD

A workflow is included at `.github/workflows/deploy.yml`.

### Required repository secrets

- `GCP_SA_KEY`: JSON service account key for deploying Cloud Functions.
- `GCP_PROJECT`: Google Cloud project ID.
- `GCP_REGION`: Cloud Functions region.
- `GCS_BUCKET`: Storage bucket used by the function.

The workflow installs dependencies, validates `main.py`, authenticates using the service account, and deploys `ingest_game_data` on push to `main` or when manually triggered.
