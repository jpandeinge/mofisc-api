# Python version matches CI. Install the same audited lock in every environment.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Install spaCy language model
RUN pip install --no-cache-dir https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl

COPY . .

# Development image
FROM base AS development
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Production image for controlled deployments that inject the private model
# and merchant lookup into the build context.
FROM base AS production-local-model

ENV LOCAL_MODEL_DIR=/app/api/app/resources/models/pfc_v4 \
    LOCAL_MODEL_ENABLED=1 \
    LOCAL_MODEL_REQUIRED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# A production image is invalid if it cannot load the checksum-verified,
# Plaid-PFC-aligned checkpoint or if the 37M-derived exact lookup is absent.
RUN python -c "from api.app.utils.local_transaction_model import require_local_model; require_local_model()" \
    && test -s /app/api/app/resources/transaction_template_lookup.bin

# Start gunicorn (migrations run in Cloud Build before deploy)
# The ML checkpoint is resident per worker. Scale Cloud Run instances
# horizontally; increase WEB_CONCURRENCY only after sizing container memory.
CMD sh -c "gunicorn -w ${WEB_CONCURRENCY:-1} -k uvicorn.workers.UvicornWorker api.main:app --bind 0.0.0.0:${PORT:-8000} --timeout 600 --graceful-timeout 120 --keep-alive 75"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD sh -c "curl --fail http://localhost:${PORT:-8000}/health || exit 1"

# Public production image. The API remains usable without the private model;
# transactions without a deterministic rule stay reviewable.
FROM base AS production

ENV LOCAL_MODEL_ENABLED=0 \
    LOCAL_MODEL_REQUIRED=0

CMD sh -c "gunicorn -w ${WEB_CONCURRENCY:-1} -k uvicorn.workers.UvicornWorker api.main:app --bind 0.0.0.0:${PORT:-8000} --timeout 600 --graceful-timeout 120 --keep-alive 75"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD sh -c "curl --fail http://localhost:${PORT:-8000}/health || exit 1"
