# Human Annotation Guide

Use `data/evaluation/human_review_seed.csv` to create the first gold benchmark.
Do not train on this file after it is annotated.

## Columns To Fill

- `gold_category`: final taxonomy category.
- `gold_merchant_name`: canonical merchant or counterparty name. Leave blank only
  when no merchant/counterparty can reasonably be identified.
- `gold_subcategory`: useful secondary label where the taxonomy supports it.
- `review_notes`: short note for uncertainty, ambiguous descriptions, or rule
  changes that should be added later.

## Review Rules

- Prefer the existing app taxonomy unless a transaction genuinely does not fit.
- Do not copy `suggested_*` labels blindly; they are weak labels from rules.
- Use the transaction text, debit/credit direction, payment type, amount bucket,
  and channel together.
- If a row is ambiguous, still choose the best category and add a note.
- Keep merchant names canonical: for example use one merchant spelling for all
  branches or noisy terminal variants.

## Output Contract

Save the reviewed file as:

```text
data/evaluation/gold_eval.csv
```

The gold file should preserve `row_id`, `model_input_text`, and all original
review columns. Future training and benchmark scripts must exclude all row IDs
listed in `holdout_manifest.json`.
