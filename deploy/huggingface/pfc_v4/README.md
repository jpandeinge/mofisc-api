---
library_name: sentence-transformers
pipeline_tag: text-classification
tags:
  - finance
  - sentence-transformers
  - scikit-learn
  - custom-handler
---

# Mofisc transaction-classifier endpoint

This directory contains the public custom-endpoint adapter for a Mofisc-compatible
transaction classifier. The trained encoder, category head, merchant index,
label configuration, and manifest are not included in this repository.

The handler expects a complete, trusted artifact directory prepared by the
repository's deployment tooling. Pickle-compatible files must never be loaded
from an untrusted source.

## Request

The endpoint accepts one transaction object or a list of up to 128 objects:

```json
{
  "inputs": [
    {
      "transaction_description": "POS PURCHASE GROCERY STORE",
      "transaction_type": "debit",
      "transaction_amount": 468.5,
      "payment_type": "card"
    }
  ]
}
```

## Response contract

Each response item includes the application category, raw model category,
confidence, optional merchant candidate, abstention flags, taxonomy, and model
version. `label` and `score` remain compatibility aliases; clients should use
the complete structured contract.

The handler rejects incomplete or checksum-invalid artifacts. Model metrics,
limitations, taxonomy, and provenance belong in the model card distributed
with each private checkpoint.
