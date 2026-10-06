# Contributing

Thank you for helping improve Mofisc.

## Before you start

- Check existing issues before opening a new one.
- Keep changes focused; separate refactors from product changes.
- Use synthetic statements and identities in tests, fixtures, and examples.
- Never commit credentials, customer data, trained private artifacts, or
  unredacted screenshots.

## Development workflow

Use Python 3.12 and an isolated environment. Install the application and lint
dependencies from the pinned requirement files, then configure `.env` with
development-only values.

Before opening a pull request, run the relevant checks:

```bash
pytest tests/unit -v
black --check --diff api/
isort --check-only --diff api/
flake8 api/ --count --select=E9,F63,F7,F82 --show-source --statistics
```

Integration tests require a running development API and services:

```bash
pytest tests/integration -v
```

## Pull requests

Describe the behavioural change, the checks you ran, migration impact, and any
new environment variables. Add or update tests for changed behaviour. Database
changes must include an Alembic migration.

By contributing, you agree that your contribution is licensed under the
repository's MIT License.
