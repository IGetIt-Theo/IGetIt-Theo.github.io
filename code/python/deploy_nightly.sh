#!/usr/bin/env bash
# ============================================================================
# Deploy cannabis_nightly.py as a scheduled Cloud Run Job
# ============================================================================
# Run this BLOCK BY BLOCK (copy each section into your terminal), not all at
# once — that way you can confirm each step before moving on. Lines starting
# with `gcloud` are the actual commands; everything else is explanation.
#
# Prereqs:
#   - gcloud CLI installed and you've run:  gcloud auth login
#   - Billing enabled on the project
#   - You ran the initial seed load already (you have)
# ============================================================================


# ── 0a. Verify your dataset's location FIRST ─────────────────────────────────
# Confirm where the data actually lives before creating infrastructure around it.
#   bq show --format=json portfolio-499022:cannabis_retail \
#     | python3 -c "import sys,json; print(json.load(sys.stdin)['location'])"
# For this project it returns: US  (a multi-region).


# ── 0b. Set variables (edit these) ───────────────────────────────────────────
# PROJECT_ID must match BQ_PROJECT in cannabis_common.py.
export PROJECT_ID="portfolio-499022"
# Your BigQuery dataset is in the US MULTI-REGION. Cloud Run cannot deploy into a
# multi-region, so pick any specific US region for the infrastructure below.
# us-central1 (Iowa) is a low-cost, central default. The job still runs its
# BigQuery work in the US multi-region regardless of where the container lives,
# so there is no cross-region penalty on the queries.
export REGION="us-central1"          # specific region for Cloud Run / Artifact Registry / Scheduler
export REPO="cannabis"               # Artifact Registry repo name
export IMAGE="cannabis-nightly"      # container image name
export JOB="cannabis-nightly-job"    # Cloud Run Job name
export SCHED="cannabis-nightly-sched"# Cloud Scheduler job name
export RUNTIME_SA="cannabis-nightly-sa"     # service account the JOB runs as
export SCHED_SA="cannabis-sched-sa"         # service account the SCHEDULER uses

gcloud config set project "$PROJECT_ID"


# ── 1. Enable the APIs you'll use ────────────────────────────────────────────
# Cloud Run, Artifact Registry (image storage), Cloud Build (builds the image),
# Cloud Scheduler (the cron), and BigQuery (the job's target).
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  bigquery.googleapis.com


# ── 2. Create an Artifact Registry repo to hold the container image ──────────
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Cannabis nightly job images"


# ── 3. Create the runtime service account + grant BigQuery access ────────────
# This identity is what the container runs as. It replaces your local
# application-default credentials. It needs to read/write your dataset.
gcloud iam service-accounts create "$RUNTIME_SA" \
  --display-name="Cannabis nightly runtime"

export RUNTIME_SA_EMAIL="${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# jobUser lets it run query jobs; dataEditor lets it append/MERGE into tables.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/bigquery.dataEditor"
# Tip: dataEditor at project level is simplest. To tighten later, grant it only
# on the cannabis_retail dataset instead of the whole project.


# ── 4. Build the container image with Cloud Build ────────────────────────────
# Run this from the directory containing the Dockerfile, requirements.txt,
# cannabis_common.py, and cannabis_nightly.py.
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE}:latest"

gcloud builds submit --tag "$IMAGE_URI"
# This uploads your code, builds the Docker image in the cloud, and stores it in
# Artifact Registry. Takes a couple of minutes the first time.


# ── 5. Create the Cloud Run Job ──────────────────────────────────────────────
# A Job runs the container to completion and exits (unlike a Service, which
# stays up serving HTTP). This is the right primitive for a nightly batch task.
gcloud run jobs create "$JOB" \
  --image="$IMAGE_URI" \
  --region="$REGION" \
  --service-account="$RUNTIME_SA_EMAIL" \
  --max-retries=1 \
  --task-timeout=900s
# task-timeout=900s (15 min) is plenty for one day's append. Bump if needed.


# ── 6. Smoke-test the job manually before scheduling ─────────────────────────
# This executes it once, right now. It should append YESTERDAY's data.
gcloud run jobs execute "$JOB" --region="$REGION"

# Watch the logs (the execute command prints a link, or use):
gcloud run jobs executions list --job="$JOB" --region="$REGION"
# Then confirm in BigQuery that yesterday's date now has rows in fact_sales.
# Because the script is idempotent, re-running won't double-load.


# ── 7. Create the scheduler's service account + let it invoke the job ────────
gcloud iam service-accounts create "$SCHED_SA" \
  --display-name="Cannabis scheduler invoker"

export SCHED_SA_EMAIL="${SCHED_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# run.invoker on this specific job lets the scheduler trigger it.
gcloud run jobs add-iam-policy-binding "$JOB" \
  --region="$REGION" \
  --member="serviceAccount:${SCHED_SA_EMAIL}" \
  --role="roles/run.invoker"


# ── 8. Create the Cloud Scheduler cron ───────────────────────────────────────
# Schedule "0 6 * * *" = every day at 06:00 in the timezone below. Pick a time a
# few hours after midnight so "yesterday" is unambiguously complete in your TZ.
# The URI is the Cloud Run Admin API "run" endpoint for your job.
gcloud scheduler jobs create http "$SCHED" \
  --location="$REGION" \
  --schedule="0 6 * * *" \
  --time-zone="America/Los_Angeles" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB}:run" \
  --http-method=POST \
  --oauth-service-account-email="${SCHED_SA_EMAIL}"


# ── 9. Test the whole chain ──────────────────────────────────────────────────
# Force the scheduler to fire now instead of waiting for 6 AM.
gcloud scheduler jobs run "$SCHED" --location="$REGION"
# Check the job executed (step 6's list command), then verify BigQuery again.

# ============================================================================
# Done. From here it runs nightly, unattended.
#
# To redeploy after editing the Python:
#   gcloud builds submit --tag "$IMAGE_URI"
#   gcloud run jobs update "$JOB" --image="$IMAGE_URI" --region="$REGION"
#
# To backfill a specific date, override the container args for one execution:
#   gcloud run jobs execute "$JOB" --region="$REGION" --args="2026-06-09"
# ============================================================================
