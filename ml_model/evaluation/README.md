# Model evaluation

These tools create transaction-model benchmark datasets and compare simple
baselines. Generated datasets and reports are intentionally excluded from Git.

The evaluation-set builder produces:

- `silver_eval.parquet` and `silver_eval.csv` for reproducible candidates;
- `human_review_seed.csv` for manual annotation;
- `holdout_manifest.json` for rows and merchants excluded from training; and
- `label_counts.json` for aggregate label coverage.

Run commands from the API repository root.

## Build an evaluation set

```bash
python ml_model/evaluation/build_eval_set.py
```

For a small smoke test:

```bash
python ml_model/evaluation/build_eval_set.py \
  --max-row-groups 1 \
  --silver-rows 500 \
  --review-rows 200 \
  --output-dir /tmp/mofisc-eval-smoke
```

The generated `model_input_text` must not contain category, merchant, or
subcategory labels.

## Run baselines

```bash
python ml_model/evaluation/run_baselines.py
```

To include a local embedding checkpoint:

```bash
python ml_model/evaluation/run_baselines.py \
  --embedding-model /path/to/embedding-model
```

Generate error summaries after the baseline run:

```bash
python ml_model/evaluation/analyze_baseline_errors.py
```

Review generated predictions before trusting aggregate metrics. Silver labels
are useful for iteration but do not replace a human-reviewed gold set. See
[`annotation_guide.md`](annotation_guide.md) for the review format.
