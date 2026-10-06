"""Offline compatibility smoke test for an injected private classifier."""

import math
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from sklearn.exceptions import InconsistentVersionWarning

from api.app.utils.local_transaction_model import (
    classify_with_local_model,
    require_local_model,
)


def main():
    warnings.simplefilter("error", InconsistentVersionWarning)
    status = require_local_model()
    if not status["ready"]:
        raise RuntimeError("Injected model did not load")
    predictions = classify_with_local_model([
        {"transaction_description": "Salary payment", "transaction_amount": 12000, "transaction_type": "credit"},
        {"transaction_description": "Grocery purchase", "transaction_amount": 200, "transaction_type": "debit"},
    ])
    if predictions is None or len(predictions) != 2:
        raise RuntimeError("Injected model did not return a prediction for each input")
    for prediction in predictions:
        if not prediction.category or not math.isfinite(prediction.confidence) or not 0 <= prediction.confidence <= 1:
            raise RuntimeError("Injected model returned an invalid prediction")
    print(f"Model compatibility passed: {status['model_version']}")


if __name__ == "__main__":
    main()
