#!/bin/bash
# Refresh PRAGMA user embeddings from the live app database.
#
# Pipeline: read-only database export -> pragmatiq validate -> tokenize (reusing the
# selected checkpoint's tokenizer) -> embed all users.
# Output: a timestamped parquet plus data/pragma/user_embeddings.parquet symlink.
#
# The pilot run (runs/galahad-pilot inside vendor/pragmatiq) was trained on
# 2026-07-22 data as a pipeline proof. Once the app has enough users for a
# real pretraining run, retrain (refit tokenizer + pretrain) and point PRAGMA_RUN at
# the new run directory.

set -euo pipefail

GALAHAD_API="$(cd "$(dirname "$0")/../.." && pwd)"
ML="$GALAHAD_API/ml_model"
RAW="$ML/data/pragma/raw"
RUN="${PRAGMA_RUN:-}"
PRAGMATIQ_BIN="${PRAGMATIQ_BIN:-pragmatiq}"
API_PYTHON="${GALAHAD_API_PYTHON:-$GALAHAD_API/.venv/bin/python}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REFRESH="$ML/data/pragma/tokenized_refresh/$STAMP"
EMBEDDINGS="$ML/data/pragma/user_embeddings_$STAMP.parquet"

if [[ -z "$RUN" ]]; then
  echo "ERROR: set PRAGMA_RUN to a versioned Pragmatiq run/checkpoint directory" >&2
  exit 2
fi
if [[ -z "${PRAGMA_PSEUDONYM_KEY:-}" ]]; then
  echo "ERROR: set PRAGMA_PSEUDONYM_KEY to a dedicated HMAC secret" >&2
  exit 2
fi
if [[ ! -x "$API_PYTHON" ]]; then
  echo "ERROR: API Python not found at $API_PYTHON" >&2
  exit 2
fi
if ! command -v "$PRAGMATIQ_BIN" >/dev/null 2>&1; then
  echo "ERROR: pragmatiq CLI not found. Install ml_model/requirements-pragma.txt" >&2
  exit 2
fi
if [[ ! -d "$RUN/tokenizer" ]]; then
  echo "ERROR: checkpoint tokenizer not found at $RUN/tokenizer" >&2
  exit 2
fi

echo "==> exporting observed multi-table histories from the database"
"$API_PYTHON" "$GALAHAD_API/api/scripts/export_pragma_dataset.py" --out "$RAW"

echo "==> validating against the pragmatiq data contract"
"$PRAGMATIQ_BIN" validate "$RAW"

echo "==> tokenizing with the checkpoint tokenizer"
mkdir -p "$(dirname "$REFRESH")"
"$PRAGMATIQ_BIN" tokenize "$RAW" --out "$REFRESH" --tokenizer-dir "$RUN/tokenizer"

echo "==> embedding users with $RUN"
"$PRAGMATIQ_BIN" embed "$REFRESH" --run "$RUN" --out "$EMBEDDINGS"

ln -sfn "$(basename "$EMBEDDINGS")" "$ML/data/pragma/user_embeddings.parquet"

echo "==> done: $EMBEDDINGS"
