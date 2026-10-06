# Experimental sequence-model integration

This directory contains an optional, offline experiment for representing a
user's ordered financial-event history. It is separate from the request-time
transaction classifier and is not required to run the API.

No sequence-model dataset, tokenizer, checkpoint, or user embedding is shipped
with this repository.

## Architecture boundary

The transaction classifier labels one transaction. A sequence model consumes
an ordered history and can support downstream tasks such as cash-flow or
recurring-payment forecasting. A collection of independent transaction rows
must not be converted into artificial user histories.

The experiment uses the Apache-2.0 Pragmatiq implementation. Its maintainers
are not affiliated with Mofisc.

## Minimum data contract

Each event should contain:

- a stable pseudonymous user identifier;
- an event timestamp and type;
- normalized numeric values such as amount; and
- reviewed categorical fields where available.

Exclude names, email addresses, phone numbers, profile images, provider
identifiers, raw account identifiers, and free text that can contain personal
information.

## Privacy requirements

- Generate pseudonyms with a dedicated secret stored outside the repository.
- Split training and evaluation by user, not by event.
- Keep raw exports, tokenized data, checkpoints, and embeddings in controlled
  storage.
- Never use production credentials from a notebook or local experiment.
- Record the schema version, source manifest, tokenizer hash, and checkpoint
  hash for reproducibility.

## Offline workflow

Install the optional dependencies:

```bash
pip install -r ml_model/requirements-pragma.txt
```

Export a de-identified dataset and validate it:

```bash
export PRAGMA_PSEUDONYM_KEY='replace-with-a-secret-manager-value'
.venv/bin/python api/scripts/export_pragma_dataset.py
pragmatiq validate ml_model/data/pragma/raw
```

Build and fit a tokenizer corpus:

```bash
.venv/bin/python ml_model/scripts/build_pragma_tokenizer_corpus.py \
  --include-live-raw ml_model/data/pragma/raw

.venv/bin/python ml_model/scripts/fit_pragma_tokenizer.py \
  ml_model/data/pragma/tokenizer_corpus \
  --out /path/to/versioned-run/tokenizer
```

Generated paths under `ml_model/data/` and `ml_model/models/` must remain
ignored. Inspect the scripts' `--help` output before using a different layout.

## Promotion gates

Do not place a sequence model in a user-facing path until it has:

- a representative, consented dataset;
- task-specific baselines and held-out-user evaluation;
- documented privacy and deletion behavior;
- reproducible model and tokenizer versions;
- calibrated downstream predictions;
- latency and cost measurements; and
- a tested rollback path.
