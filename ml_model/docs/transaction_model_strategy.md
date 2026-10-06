# Transaction model strategy

Mofisc uses a staged classification pipeline. Deterministic rules handle known
patterns first; statistical models handle ambiguous transactions only when
their confidence is high enough.

## Output contract

A classifier candidate should return:

- `category`: a fixed budgeting and reporting category;
- `merchant_name`: a canonical merchant when the evidence is sufficient;
- `subcategory`: an optional, more specific label;
- `confidence`: a calibrated score used by the acceptance gate; and
- `source`: the rule, retrieval, classifier, or fallback path used.

Unknown merchants should remain unknown instead of being force-matched.

## Public and private boundary

This repository includes the runtime rule set, model interfaces, notebooks,
training utilities, and evaluation tools. It does not include transaction
datasets, customer records, trained production checkpoints, generated lookup
artifacts, or private benchmark reports.

Use only appropriately licensed and de-identified data. Keep all generated
datasets, checkpoints, and reports outside version control.

## Recommended pipeline

1. **Rule engine**
   - Match versioned, reviewed patterns.
   - Return the matching rule identifier and deterministic labels.
2. **Merchant retrieval**
   - Encode transaction text that contains no target labels.
   - Retrieve canonical merchant candidates from the training partition only.
3. **Category classifier or reranker**
   - Combine transaction fields, rule evidence, and retrieval candidates.
   - Predict labels from the supported taxonomy.
4. **Confidence gate**
   - Accept only predictions that meet measured precision targets.
   - Route uncertain results to a fallback label or review.
5. **Feedback loop**
   - Treat reviewed corrections as candidate labels.
   - Retain the original prediction, model version, confidence, and edit.

## Evaluation contract

Compare every candidate with rule-only, majority-label, and retrieval
baselines. At minimum, report:

- category macro-F1 and weighted-F1;
- merchant top-1 and top-5 accuracy;
- unknown-merchant false-link rate;
- coverage at selected precision thresholds;
- calibration by confidence bucket; and
- latency and batch throughput.

Prevent leakage by holding out complete merchants, excluding evaluation rows
from training, and building model input without category or merchant labels.
Keep a versioned manifest of every split.

## Development sequence

1. Build a benchmark with `ml_model/evaluation/build_eval_set.py`.
2. Review and annotate a small gold set.
3. Run the baselines with `ml_model/evaluation/run_baselines.py`.
4. Inspect errors with `ml_model/evaluation/analyze_baseline_errors.py`.
5. Train a candidate only after the benchmark and taxonomy are stable.
6. Promote it only when it passes accuracy, calibration, privacy, latency, and
   rollback checks.

The API can run without a trained model. In that mode it uses the public rule
classifier and conservative fallbacks.
