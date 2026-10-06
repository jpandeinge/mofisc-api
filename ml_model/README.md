# Transaction model workspace

This directory contains the reproducible parts of Mofisc's transaction-model
workflow: data auditing, taxonomy design, corpus preparation, training, and
evaluation.

The production dataset and trained checkpoint are private and are not included
in this repository. Bring your own appropriately licensed, de-identified data
and keep generated datasets and model artifacts outside version control.

## Setup

Run commands from the API repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r ml_model/requirements.txt
```

The notebooks are checked in without execution output or local filesystem
paths. Open them in numerical order:

| Notebook | Purpose |
| --- | --- |
| `02_data_audit_and_schema_recovery.ipynb` | Inspect source data and recover a stable schema. |
| `03_taxonomy_and_label_consolidation.ipynb` | Consolidate category and merchant labels. |
| `04_train_category_merchant_model.ipynb` | Train and evaluate a category/merchant model. |
| `05_taxonomy_v4_international.ipynb` | Prepare the international taxonomy revision. |
| `06_train_pfc_model.ipynb` | Train the current PFC classifier. |

## Evaluation

Build a small evaluation set:

```bash
python ml_model/evaluation/build_eval_set.py \
  --max-row-groups 1 \
  --silver-rows 500 \
  --review-rows 200 \
  --output-dir /tmp/mofisc-eval-smoke
```

Run the rule-based baselines:

```bash
python ml_model/evaluation/run_baselines.py
python ml_model/evaluation/analyze_baseline_errors.py
```

See [`evaluation/README.md`](evaluation/README.md) for the generated files and
annotation flow, and [`docs/transaction_model_strategy.md`](docs/transaction_model_strategy.md)
for the evaluation and promotion criteria.

## Optional PRAGMA work

The PRAGMA experiments are separate from the request-time classifier. Their
privacy gates and current implementation status are documented in
[`docs/PRAGMA_INTEGRATION.md`](docs/PRAGMA_INTEGRATION.md). Install the separate
dependency set only when working on that path:

```bash
pip install -r ml_model/requirements-pragma.txt
```
