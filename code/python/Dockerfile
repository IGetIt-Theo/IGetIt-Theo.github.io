# Slim Python base keeps the image small and fast to cold-start.
FROM python:3.12-slim

# Don't buffer stdout/stderr — logs show up in Cloud Logging immediately.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first so this layer caches when only your code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Your code. cannabis_common.py must be present — cannabis_nightly imports it.
COPY cannabis_common.py cannabis_nightly.py ./

# Cloud Run Jobs just run the container's entrypoint to completion.
# No argument -> the script defaults to "yesterday", which is exactly what we want.
ENTRYPOINT ["python", "cannabis_nightly.py"]
