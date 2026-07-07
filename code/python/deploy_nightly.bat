@echo off
REM ============================================================================
REM Deploy cannabis_nightly.py as a scheduled Cloud Run Job  (Windows CMD version)
REM ============================================================================
REM Run this BLOCK BY BLOCK by copy/pasting each section into Command Prompt.
REM Do NOT run the whole file at once the first time — you want to see each step
REM succeed. Lines starting with REM are comments; everything else is a command.
REM
REM IMPORTANT: run all of this from the FOLDER that contains your files:
REM   cannabis_nightly.py, cannabis_common.py, Dockerfile, requirements.txt
REM e.g.:  cd C:\Users\theo\OneDrive\Claude\IGetIt-Theo.github.io\code\python
REM ============================================================================


REM ── 0a. Verify your dataset location (optional sanity check) ────────────────
REM   bq show --format=json portfolio-499022:cannabis_retail
REM Look for "location": "US". (We already know it's US.)


REM ── 0b. Set variables ───────────────────────────────────────────────────────
REM In CMD: "set NAME=value" with NO spaces around =, and reference as %NAME%.
set PROJECT_ID=portfolio-499022
set REGION=us-central1
set REPO=cannabis
set IMAGE=cannabis-nightly
set JOB=cannabis-nightly-job
set SCHED=cannabis-nightly-sched
set RUNTIME_SA=cannabis-nightly-sa
set SCHED_SA=cannabis-sched-sa

gcloud config set project %PROJECT_ID%


REM ── 1. Enable the APIs ──────────────────────────────────────────────────────
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com bigquery.googleapis.com


REM ── 2. Create the Artifact Registry repo (holds the container image) ────────
gcloud artifacts repositories create %REPO% --repository-format=docker --location=%REGION% --description="Cannabis nightly job images"


REM ── 3. Create the runtime service account + grant BigQuery access ───────────
gcloud iam service-accounts create %RUNTIME_SA% --display-name="Cannabis nightly runtime"

REM Build the full SA email into a variable for reuse:
set RUNTIME_SA_EMAIL=%RUNTIME_SA%@%PROJECT_ID%.iam.gserviceaccount.com

gcloud projects add-iam-policy-binding %PROJECT_ID% --member="serviceAccount:%RUNTIME_SA_EMAIL%" --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding %PROJECT_ID% --member="serviceAccount:%RUNTIME_SA_EMAIL%" --role="roles/bigquery.dataEditor"


REM ── 4. Build the container image from your LOCAL files ──────────────────────
REM This uploads the current folder, builds the image in the cloud, and stores
REM it in Artifact Registry. Run from the folder with your .py files + Dockerfile.
set IMAGE_URI=%REGION%-docker.pkg.dev/%PROJECT_ID%/%REPO%/%IMAGE%:latest

gcloud builds submit --tag %IMAGE_URI%


REM ── 5. Create the Cloud Run Job ─────────────────────────────────────────────
gcloud run jobs create %JOB% --image=%IMAGE_URI% --region=%REGION% --service-account=%RUNTIME_SA_EMAIL% --max-retries=1 --task-timeout=900s


REM ── 6. Smoke-test the job once, right now ───────────────────────────────────
gcloud run jobs execute %JOB% --region=%REGION%

REM See execution status / logs:
gcloud run jobs executions list --job=%JOB% --region=%REGION%
REM Then confirm in BigQuery that yesterday's date now has rows in fact_sales.


REM ── 7. Create the scheduler service account + let it invoke the job ─────────
gcloud iam service-accounts create %SCHED_SA% --display-name="Cannabis scheduler invoker"

set SCHED_SA_EMAIL=%SCHED_SA%@%PROJECT_ID%.iam.gserviceaccount.com

gcloud run jobs add-iam-policy-binding %JOB% --region=%REGION% --member="serviceAccount:%SCHED_SA_EMAIL%" --role="roles/run.invoker"


REM ── 8. Create the Cloud Scheduler cron ──────────────────────────────────────
REM "0 6 * * *" = 6:00 AM daily in the timezone below. Container computes
REM "yesterday" in UTC; 6 AM Pacific is mid-afternoon UTC, so yesterday is a
REM complete day. Change the timezone if you prefer your local clock.
gcloud scheduler jobs create http %SCHED% --location=%REGION% --schedule="0 6 * * *" --time-zone="America/Los_Angeles" --uri="https://%REGION%-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/%PROJECT_ID%/jobs/%JOB%:run" --http-method=POST --oauth-service-account-email=%SCHED_SA_EMAIL%


REM ── 9. Test the whole chain (force the scheduler to fire now) ───────────────
gcloud scheduler jobs run %SCHED% --location=%REGION%

REM ============================================================================
REM Done — it now runs nightly, unattended.
REM
REM Redeploy after editing Python:
REM   gcloud builds submit --tag %IMAGE_URI%
REM   gcloud run jobs update %JOB% --image=%IMAGE_URI% --region=%REGION%
REM
REM Backfill a specific date (override the container args for one run):
REM   gcloud run jobs execute %JOB% --region=%REGION% --args="2026-06-15"
REM ============================================================================
