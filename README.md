# Mofisc API

Mofisc API is the FastAPI backend for statement ingestion, transaction review,
financial insights, planning, notifications, and account features used by the
Mofisc web application.

> **Project status:** active pre-release software. The API processes sensitive
> financial data. Use synthetic fixtures in development and review the security
> guidance before deploying it with real customer data.

## What is included

- FastAPI routes with OpenAPI documentation
- Supabase authentication and PostgreSQL persistence
- Bank-statement extraction with native-text and OCR fallbacks
- Rule-based transaction categorisation and correction workflows
- Budgets, goals, financial metrics, and calculators
- Persistent in-app notifications with optional email, push, and SMS delivery
- Redis-backed rate limiting and Celery tasks
- Database migrations and unit/integration tests

The trained production transaction model and merchant lookup are private
deployment artifacts. They are intentionally not part of the public source
distribution; see [Transaction models](#transaction-models).

## Requirements

- Python 3.12
- PostgreSQL or a Supabase project
- Redis
- Poppler and Tesseract 5 for statement extraction
- Docker and Docker Compose, optional

## Local setup

Clone the repository and create an isolated environment:

```bash
git clone https://github.com/jpandeinge/mofisc-api.git
cd mofisc-api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock
cp .env.example .env
```

Install the local PDF tools:

```bash
# macOS
brew install poppler tesseract

# Ubuntu or Debian
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-eng
```

Configure `.env` with development-only PostgreSQL/Supabase credentials and
random local secrets. Do not reuse production credentials.

Start Redis, apply migrations, and run the API:

```bash
docker compose up -d redis
alembic upgrade head
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Useful endpoints:

- API health: <http://localhost:8000/health>
- Swagger UI: <http://localhost:8000/docs>
- OpenAPI schema: <http://localhost:8000/openapi.json>

## Docker Compose

The compose file runs Redis, the API, Celery worker, Celery Beat, and Flower.
It does **not** create PostgreSQL or Supabase; `DATABASE_URL` and the Supabase
settings must point to a development instance you control.

```bash
cp .env.example .env
docker compose up --build
```

Services are available on ports `8000` (API), `5555` (Flower), and `6379`
(Redis).

## Configuration

`.env.example` is the authoritative configuration template. The main groups
are:

| Group | Required for local API | Notes |
| --- | --- | --- |
| Application secrets | Yes | Generate unique local values |
| PostgreSQL/Supabase | Yes | Use development-only credentials |
| Redis | Yes | Defaults to `localhost:6379` outside Compose |
| Transaction model | No | Rules remain available without private artifacts |
| Firebase | No | Required only for background browser/mobile push |
| SendGrid | No | Enables email delivery |
| SMS provider | No | Enables SMS delivery |
| Sentry | No | Enables error reporting |
| Payments | No | Required only for paid-plan flows |

Never commit `.env`, service-account JSON, certificates, private keys, bank
statements, logs, exports, or customer data.

## Transaction models

The repository contains model integration code and conservative local rules,
but not the trained production checkpoint or the production merchant-template
lookup.

For development without private artifacts, set:

```dotenv
LOCAL_MODEL_ENABLED=0
LOCAL_MODEL_REQUIRED=0
PFC_MODEL_REQUIRED=0
```

Transactions that cannot be classified by available rules remain reviewable
instead of receiving a fabricated high-confidence result. Production operators
may provide a compatible local artifact through `LOCAL_MODEL_DIR` or configure
their own endpoint. Do not download or deserialize untrusted pickle-compatible
model files.

## Tests and checks

Run the unit suite:

```bash
pytest tests/unit -v
```

The API integration tests expect a running development API and are skipped
when it is unavailable:

```bash
pytest tests/integration -v
```

Install the pinned lint tools and run the blocking CI checks:

```bash
python -m pip install -r requirements-lint.txt
black --check --diff api/
isort --check-only --diff api/
flake8 api/ --count --select=E9,F63,F7,F82 --show-source --statistics
```

To apply formatting:

```bash
isort api/
black api/
```

CI additionally runs dependency audits, Bandit, advisory mypy/Flake8 checks,
the Firebase Functions suite, and an offline model compatibility check when
the private artifact is available in the deployment environment.

## Project map

```text
api/
  app/
    config/       Runtime settings
    middleware/   Security, errors, and rate limiting
    models/       Database models
    routes/       HTTP endpoints
    services/     Domain and integration services
    utils/        Statement parsing and classification utilities
  main.py         FastAPI application
db/migrations/    Alembic migrations
firebase-functions/functions/  Optional SMS function
tests/            Unit and integration tests
ml_model/         Reproducible training and evaluation source
```

Focused design notes live in `docs/`. Keep operational secrets and private
deployment runbooks outside the public repository.

## Security and privacy

- Use synthetic statements in tests and examples.
- Keep server credentials out of frontend configuration.
- Validate uploaded files and preserve the existing size and rate limits.
- Do not log statement text, access tokens, or provider responses containing
  customer information.
- Report vulnerabilities privately as described in [`SECURITY.md`](SECURITY.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development workflow and pull
request expectations.

## License

Released under the [MIT License](LICENSE).
