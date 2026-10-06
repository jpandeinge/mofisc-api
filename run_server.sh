#!/bin/bash
# Helper script to run the Galahad API server with the correct virtual environment

# Change to the script's directory
cd "$(dirname "$0")"

# Activate the .venv virtual environment
source .venv/bin/activate

# Run uvicorn with proper reload configuration
uvicorn api.main:app \
  --reload \
  --reload-dir api \
  --reload-exclude '.venv/*' \
  --reload-exclude 'venv/*' \
  --reload-exclude '__pycache__/*' \
  --reload-exclude '*.pyc' \
  --reload-exclude 'wandb/*' \
  --reload-exclude 'ml_model/*' \
  --host 0.0.0.0 \
  --port 8000
